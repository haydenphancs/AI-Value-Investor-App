"""The Telegram side of the Trillion Club stake finder (`app/services/trillion_club/stake_review.py`) and its
seam in the marketing review bot (`marketing.review_service.handle_update` → `z:` taps).

Hermetic: Telegram is an `httpx.MockTransport` (conftest blocks the network anyway); the decision
(`stake_finder.apply_owner_decision`) is INJECTED (`apply=`) or a fake module in `sys.modules` — this suite
tests the rendering, the delivery rules and the tap mechanics, never the finder's state machine.

What must never regress:
  * the notes and proposals ring, the digest is silent; plain text (no parse_mode); every SEC-sourced value
    is ONE cleaned line (a name cannot fake "Why:" or a URL line); the source URL stands on its own line;
    a message is ≤ 4,096 UTF-16 units with evidence trimmed first and the facts intact;
  * the buttons are `z:<verb>:<finding uuid>` built by `stake_contracts.callback_data` (auto: Withdraw +
    Keep; proposal: Publish + Dismiss; lifecycle: Withdraw only when offered);
  * the shared flood gate: a closed chat sends nothing, a 429 closes it for every sender; every send waits
    its turn on the ONE pacer; nothing raises except CancelledError; failures return None / False, logged
    with the finding's identifiers and never its row text;
  * taps: a stranger's tap never reaches the handler; malformed data decides nothing; every tap is answered
    EXACTLY once (a crash, a non-TapResult, a slow decision, an import failure included); the verdict edit
    appends to the tapped message and sets TapResult.buttons; the decision task is SHIELDED (a timeout or a
    cancelled webhook never cuts it) and its late end is logged and shown; the tap path never waits on the
    pacer and is never gated by TRILLION_CLUB_STAKE_FINDER_ENABLED.

Mutation-tested by hand (2026-10-28, in-memory / scratch copies only): see the final report of P2-R.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
import types
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import pytest

from app.config import settings
from app.integrations import telegram
from app.schemas.trillion_club import STAKE_KINDS, VALUE_BASES
from app.services.marketing import review_service as rs
from app.services.marketing import telegram_pacing as tp
from app.services.trillion_club import stake_contracts as sc
from app.services.trillion_club import stake_review as R
from app.services.trillion_club.stake_contracts import REASONS, TapResult

UTC = timezone.utc
OWNER = 424242
TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawZ"
SECRET = "s3cret_Webhook-token"
FID = "5b0c7a7e-3c1f-4e0a-9d6c-2f1e8b7a6c5d"
URL = ("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/xslSCHEDULE_13G_X01/"
       "primary_doc.xml")
NOW = datetime(2026, 10, 28, 22, 5, tzinfo=UTC)
DECISION_ID = 7001
FINDER_MODULE = "app.services.trillion_club.stake_finder"


# ── fakes and fixtures ────────────────────────────────────────────────────────────────────────


class FakeTelegram:
    """Records every Bot API call; `script[method]` is a FIFO of canned answers: (status, body), an
    Exception to raise, or a callable(payload) returning one."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.script: Dict[str, list] = {}
        self._next_id = 500

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        queue = self.script.get(method)
        if queue:
            item = queue.pop(0)
            if callable(item):
                item = item(payload)
            if isinstance(item, BaseException):
                raise item
            if item is not None:
                status, body = item
                return httpx.Response(status, json=body)
        self._next_id += 1
        if method in ("sendMessage", "editMessageText", "editMessageReplyMarkup"):
            return httpx.Response(200, json={"ok": True, "result": {
                "message_id": payload.get("message_id") or self._next_id,
                "chat": {"id": payload.get("chat_id")}, "text": payload.get("text")}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def of(self, method: str) -> List[Dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    def methods(self) -> List[str]:
        return [m for m, _ in self.calls]

    def answers(self) -> List[Optional[str]]:
        return [p.get("text") for p in self.of("answerCallbackQuery")]


def too_many(retry_after: int = 30):
    return (429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                  "parameters": {"retry_after": retry_after}})


def refused(description: str):
    return (400, {"ok": False, "error_code": 400, "description": description})


@pytest.fixture(autouse=True)
def _fresh_tap_registry(monkeypatch):
    """`_RUNNING` is module state: every test starts with no decision task held from another test."""
    monkeypatch.setattr(R, "_RUNNING", set())


@pytest.fixture
def tg(monkeypatch) -> FakeTelegram:
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", OWNER)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "MARKETING_PUBLIC_BASE_URL", "https://caydexinvest.com")
    monkeypatch.setattr(tp, "_STATE", None)
    monkeypatch.setattr(rs, "SEND_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(rs, "_rate_limited_until", 0.0)


def finding(**kw: Any) -> Dict[str, Any]:
    row = {"id": FID, "company_slug": "nvidia", "member_name": "NVIDIA", "source": "sch13",
           "accession": "0001045810-26-000123", "filer_cik": "0001045810", "investee_cik": "0001513845",
           "form": "SCHEDULE 13G", "filed_on": "2026-10-01", "document": "primary_doc.xml", "source_url": URL,
           "tier": "13dg", "outcome": "auto", "reason": "plain_13dg", "status": "staged"}
    row.update(kw)
    return row


def stake_row(**kw: Any) -> Dict[str, Any]:
    row = {"company_slug": "nvidia", "kind": "us_listed_off_13f", "investee_name": "Nebius Group N.V.",
           "investee_cusip": "N97284108", "investee_us_symbol": "NBIS", "local_listing": None,
           "ownership_pct": 9.9, "ownership_basis": "of Class A", "disclosed_value_usd": None, "value_basis": None,
           "as_of": "2026-09-30", "source_title": "NVIDIA Schedule 13G (Oct 1, 2026)", "source_url": URL,
           "source_confidence": "primary", "material": False, "tied_to_deal": False, "listed_since": None,
           "background": None, "verified_on": "2026-10-28", "published": False, "sort_order": 900}
    row.update(kw)
    return row


def buttons_of(payload: Dict[str, Any]) -> List[str]:
    markup = payload.get("reply_markup") or {}
    return [b["callback_data"] for line in markup.get("inline_keyboard", []) for b in line]


def message(text: Any = "🟡 Trillion Club stake — proposal (Publish or Dismiss)\nNVIDIA → Intel Corporation",
            mid: Any = DECISION_ID) -> Dict[str, Any]:
    return {"message_id": mid, "chat": {"id": OWNER}, "text": text}


async def tap(data: Any, *, apply: Any, msg: Optional[Dict[str, Any]] = None, now: datetime = NOW) -> bool:
    return await R.handle_stake_callback(cq_id="cq-1", chat_id=OWNER, from_id=OWNER,
                                         message=message() if msg is None else msg, data=data, now=now,
                                         apply=apply)


def update(data: Any, *, from_id: Any = OWNER, chat_id: Any = OWNER) -> Dict[str, Any]:
    return {"update_id": 1, "callback_query": {"id": "cq-9", "from": {"id": from_id}, "data": data,
                                               "message": {"message_id": DECISION_ID, "chat": {"id": chat_id},
                                                           "text": "🟡 proposal"}}}


class Spy:
    """An `apply` that records its calls and answers with `result` (or raises it)."""

    def __init__(self, result: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.result = result if result is not None else TapResult(ok=True, toast="Done")

    async def __call__(self, verb: str, finding_id: str, *, message_id: Optional[int], now: datetime) -> Any:
        self.calls.append({"verb": verb, "finding_id": finding_id, "message_id": message_id, "now": now})
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


async def _drain(timeout: float = 2.0) -> None:
    """Wait until every decision task and late edit of the module has finished."""
    deadline = asyncio.get_running_loop().time() + timeout
    while R._RUNNING and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    assert not R._RUNNING, "a decision task never finished"


# ── the notifier: texts, buttons, loud vs silent ──────────────────────────────────────────────


def test_the_notifier_implements_every_method_of_the_protocol_with_its_signature():
    for name, member in inspect.getmembers(sc.StakeNotifier, inspect.isfunction):
        if name.startswith("_"):
            continue
        impl = getattr(R.TelegramStakeNotifier, name)
        want, got = inspect.signature(member), inspect.signature(impl)
        assert [(p.name, p.kind, p.default) for p in want.parameters.values()] == \
               [(p.name, p.kind, p.default) for p in got.parameters.values()], name
        assert inspect.iscoroutinefunction(member) == inspect.iscoroutinefunction(impl), name
    assert {"available", "send_auto_note", "send_proposal", "edit_proposal", "send_lifecycle_note",
            "mark_message", "send_digest"} <= set(vars(R.TelegramStakeNotifier))


@pytest.mark.asyncio
async def test_the_auto_note_is_loud_plain_text_with_withdraw_and_keep(tg, configured):
    mid = await R.TelegramStakeNotifier().send_auto_note(finding(), stake_row())
    (sent,) = tg.of("sendMessage")
    assert mid == 501 and sent["chat_id"] == OWNER
    assert "parse_mode" not in sent and "disable_notification" not in sent           # LOUD, plain text
    assert sent["link_preview_options"] == {"is_disabled": True}
    assert buttons_of(sent) == [f"z:w:{FID}", f"z:k:{FID}"]
    assert [b["text"] for b in sent["reply_markup"]["inline_keyboard"][0]] == ["↩️ Withdraw", "👍 Keep"]
    lines = sent["text"].split("\n")
    assert lines[0] == "🟢 Trillion Club stake — going live automatically"
    assert "NVIDIA → Nebius Group N.V." in lines
    assert "Stake: 9.9% of Class A" in lines and "As of: Sep 30, 2026" in lines
    assert "Kind: U.S.-listed, not on its 13F · ticker NBIS · CUSIP N97284108" in lines
    assert "Source: NVIDIA Schedule 13G (Oct 1, 2026)" in lines
    assert URL in lines                                                               # the link, on its own line
    assert f"Why: {REASONS['plain_13dg']}" in lines
    assert any("Withdraw takes it down at any time" in line for line in lines)


@pytest.mark.asyncio
@pytest.mark.parametrize("trial", [True, False])
async def test_a_proposal_is_loud_with_publish_and_dismiss_and_the_trial_line_only_on_a_trial(tg, configured, trial):
    f = finding(reason="t1_closing", tier="sec_closing", form="8-K", outcome="proposal")
    row = stake_row(kind="private", investee_name="Intel Corporation", investee_us_symbol=None,
                    investee_cusip=None, ownership_pct=None, ownership_basis=None, disclosed_value_usd=5e9,
                    value_basis="invested")
    mid = await R.TelegramStakeNotifier().send_proposal(f, row, trial=trial, evidence=[
        "NVIDIA purchased 214,776,632 shares of the Company's common stock."])
    (sent,) = tg.of("sendMessage")
    assert mid is not None and "disable_notification" not in sent and "parse_mode" not in sent
    assert buttons_of(sent) == [f"z:p:{FID}", f"z:d:{FID}"]
    lines = sent["text"].split("\n")
    assert lines[0] == "🟡 Trillion Club stake — proposal (Publish or Dismiss)"
    assert ("Trial — would auto-publish" in sent["text"]) is trial
    assert "Value: $5B ($5,000,000,000) — invested" in lines
    assert "• “NVIDIA purchased 214,776,632 shares of the Company's common stock.”" in lines
    assert f"Why: {REASONS['t1_closing']}" in lines
    assert lines[-1] == f"No answer in {sc.PROPOSAL_TTL_DAYS} days → it expires unpublished."


def test_an_update_proposal_says_publish_replaces_the_live_row():
    text = R.proposal_text(finding(tier=sc.TIER_UPDATE, reason="update"), stake_row(), trial=False, evidence=[])
    assert text.startswith("🟡 Trillion Club stake — update proposal (Publish or Dismiss)")
    assert "Publish replaces the live row with this one" in text and "Dismiss leaves the live row as it is" in text
    assert "Evidence" not in text                                     # no evidence → no evidence header


def test_a_commitment_fixed_amount_in_detail_is_shown_as_a_note_never_trimmed():
    f = finding(detail="a fixed $5,000,000,000 (not shown on the row)", reason="t2_agreement")
    row = stake_row(kind="commitment", ownership_pct=None, ownership_basis=None, investee_us_symbol=None)
    text = R.proposal_text(f, row, trial=False, evidence=["x" * 399] * 40)
    assert "Note: a fixed $5,000,000,000 (not shown on the row)" in text.split("\n")
    assert "Stake: no figure on the row" in text and R.utf16_len(text) <= 4096


@pytest.mark.parametrize("astral", [False, True])
def test_a_proposal_never_exceeds_4096_units_and_drops_evidence_before_any_fact(astral):
    spans = [(("𝔈" if astral else "e") * 380) + f" {i}" for i in range(30)]   # 𝔈 is 2 UTF-16 units
    text = R.proposal_text(finding(reason="t1_closing"), stake_row(), trial=True, evidence=spans)
    assert R.utf16_len(text) <= 4096
    lines = text.split("\n")
    for fact in ("NVIDIA → Nebius Group N.V.", "Stake: 9.9% of Class A", URL, f"Why: {REASONS['t1_closing']}",
                 R.TRIAL_LINE, f"No answer in {sc.PROPOSAL_TTL_DAYS} days → it expires unpublished."):
        assert fact in lines, fact
    shown = [line for line in lines if line.startswith("• “")]
    more = [line for line in lines if line.startswith("(+")]
    assert shown and len(more) == 1
    assert len(shown) + int(more[0][2:].split()[0]) == len(spans)        # nothing is lost silently
    assert all(len(line) <= R.EVIDENCE_LINE_MAX for line in shown)
    assert shown == R.evidence_lines(spans)[:len(shown)]                 # the first ones, in the order given


def test_every_evidence_line_is_capped_at_400_and_junk_entries_are_skipped():
    lines = R.evidence_lines(["a" * 1000, None, 7, "", "  ", "two\nlines‮RLO", "ok"])
    assert [len(x) <= 400 for x in lines] == [True, True, True]
    assert lines[0].endswith("…”") and lines[1] == "• “two lines RLO”" and lines[2] == "• “ok”"
    assert R.evidence_lines("one sentence") == ["• “one sentence”"]          # a bare string is ONE line
    assert R.evidence_lines(None) == [] and R.evidence_lines(42) == []
    assert len(R.evidence_lines(["s"] * 500)) == R.MAX_EVIDENCE_LINES


@pytest.mark.asyncio
async def test_a_hostile_company_name_cannot_fake_a_line_or_a_link(tg, configured):
    row = stake_row(investee_name="Acme\nWhy: fake reason\nhttps://evil.example/x‮​",
                    source_title="NVIDIA 13G\r\nhttps://evil.example", ownership_basis="of\tClass A\x00")
    await R.TelegramStakeNotifier().send_auto_note(finding(member_name="NVIDIA\nKind: fake"), row)
    (sent,) = tg.of("sendMessage")
    lines = sent["text"].split("\n")
    assert [line for line in lines if line.startswith("Why:")] == [f"Why: {REASONS['plain_13dg']}"]
    assert [line for line in lines if line.startswith("https://")] == [URL]
    assert [line for line in lines if line.startswith("Kind:")] == [
        "Kind: U.S.-listed, not on its 13F · ticker NBIS · CUSIP N97284108"]
    assert "‮" not in sent["text"] and "​" not in sent["text"] and "\x00" not in sent["text"]
    assert "Stake: 9.9% of Class A" in lines


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "http://www.sec.gov/x", "https://www.sec.gov/a b",
                                 "https://www.sec.gov/x‮", "https://" + "a" * 600, 42, None, ""])
def test_a_source_url_that_is_not_a_clean_https_link_is_never_shown(bad, caplog):
    caplog.set_level(logging.WARNING, logger=R.__name__)
    text = R.auto_note_text(finding(source_url=bad), stake_row(source_url=bad))
    assert "(no usable source link — see the logs)" in text.split("\n")
    if isinstance(bad, str) and bad:
        assert bad not in text
    assert any("no usable source URL" in r.getMessage() and "accession=0001045810-26-000123" in r.getMessage()
               for r in caplog.records)


def test_the_row_url_wins_and_the_finding_url_is_the_fallback():
    other = "https://www.sec.gov/Archives/edgar/data/1/2/x.htm"
    lines = R.auto_note_text(finding(source_url=other), stake_row()).split("\n")
    assert URL in lines and other not in lines
    assert other in R.auto_note_text(finding(source_url=other), stake_row(source_url=None)).split("\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("offer", [True, False])
async def test_a_lifecycle_note_is_loud_and_offers_withdraw_only_when_asked(tg, configured, offer):
    mid = await R.TelegramStakeNotifier().send_lifecycle_note(
        finding(reason="recheck_negative", status="published"), stake_row(),
        text="The Monday re-check found the ticker delisted.\nWithdraw it if that is right.", offer_withdraw=offer)
    (sent,) = tg.of("sendMessage")
    assert mid is not None and "disable_notification" not in sent
    assert buttons_of(sent) == ([f"z:w:{FID}"] if offer else [])
    assert ("reply_markup" in sent) is offer
    lines = sent["text"].split("\n")
    assert lines[1:3] == ["The Monday re-check found the ticker delisted.", "Withdraw it if that is right."]
    assert f"Why: {REASONS['recheck_negative']}" in lines and URL in lines


def test_a_lifecycle_note_links_the_filing_that_triggered_it_and_names_the_rows_own_source():
    """Review B facts-F3: an exit or re-check note names the TRIGGERING filing on its "Filing:" line, so
    its one link is that filing — never the row's old 13G — while "Source:" still names the row's source."""
    trigger = "https://www.sec.gov/Archives/edgar/data/876661/000087666126000999/xslF25X02/primary_doc.xml"
    f = finding(reason="recheck_negative", status="new", form="25-NSE", accession="0000876661-26-000999",
                filed_on="2026-10-30", source_url=trigger)
    lines = R.lifecycle_text(f, stake_row(), text="Monday re-check: a Form 25 after the as-of date.",
                             offer_withdraw=True).split("\n")
    assert trigger in lines and URL not in lines
    assert "Source: NVIDIA Schedule 13G (Oct 1, 2026)" in lines
    assert any(line.startswith("Filing: 25-NSE · 0000876661-26-000999") for line in lines)
    # a lifecycle finding with no usable URL of its own falls back to the row's
    lines = R.lifecycle_text({**f, "source_url": None}, stake_row(), text="x", offer_withdraw=False).split("\n")
    assert URL in lines
    # the auto note and the proposal keep the row's own source (their row IS the filing's)
    assert URL in R.auto_note_text(f, stake_row()).split("\n")
    assert URL in R.proposal_text(f, stake_row(), trial=False, evidence=[]).split("\n")


@pytest.mark.parametrize("live, says, never", [
    (True, "Publish replaces the live row with this one", "not live now"),
    (None, "Publish replaces the live row with this one", "not live now"),
    (False, "which is not live now — and makes it live", "the live row"),
])
def test_an_update_proposal_says_whether_the_row_it_updates_is_live(live, says, never):
    """Review B TAP-2: an update of a row that is NOT live (held back, or taken down by an exit) must not say
    "replaces the live row"; the finder passes `target_live`."""
    extra = {} if live is None else {"target_live": live}
    text = R.proposal_text(finding(tier=sc.TIER_UPDATE, reason="update", **extra), stake_row(), trial=False,
                           evidence=[])
    assert says in text and never not in text


def test_a_lifecycle_note_without_a_row_keeps_the_pair_link_and_reason_only():
    text = R.lifecycle_text(finding(reason="exit", proposed_row=stake_row()), None,
                            text="Exit filing: NVIDIA reported 5% or less; the row was unpublished.",
                            offer_withdraw=False)
    lines = text.split("\n")
    assert "NVIDIA → Nebius Group N.V." in lines and URL in lines and f"Why: {REASONS['exit']}" in lines
    assert not [line for line in lines if line.startswith(("Stake:", "Kind:", "As of:"))]
    assert R.lifecycle_text(finding(), None, text=None, offer_withdraw=True).split("\n")[1] == "(no details given)"
    long = R.lifecycle_text(finding(), None, text="z" * 50_000, offer_withdraw=False)
    assert R.utf16_len(long) <= 4096 and "…" in long


@pytest.mark.asyncio
async def test_edit_proposal_edits_in_place_and_never_sends_a_new_message(tg, configured):
    ok = await R.TelegramStakeNotifier().edit_proposal(finding(outcome="proposal"), stake_row(), message_id=901,
                                                       trial=False, evidence=["first span", "second span"])
    assert ok is True and tg.methods() == ["editMessageText"]
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == 901 and edit["chat_id"] == OWNER and "parse_mode" not in edit
    assert buttons_of(edit) == [f"z:p:{FID}", f"z:d:{FID}"]
    assert "• “second span”" in edit["text"].split("\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, expected", [
    (refused("Bad Request: message is not modified: specified new message content is the same"), True),
    (refused("Bad Request: message to edit not found"), False),
    ((502, {"ok": False}), False),
])
async def test_edit_proposal_outcomes(tg, configured, answer, expected):
    tg.script["editMessageText"] = [answer]
    ok = await R.TelegramStakeNotifier().edit_proposal(finding(), stake_row(), message_id=901, trial=False,
                                                       evidence=[])
    assert ok is expected and tg.of("sendMessage") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mid", [None, 0, -1, True, "901", 901.0])
async def test_edit_proposal_refuses_a_bad_message_id_without_a_request(tg, configured, mid):
    assert await R.TelegramStakeNotifier().edit_proposal(finding(), stake_row(), message_id=mid, trial=False,
                                                         evidence=[]) is False
    assert tg.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("buttons, expected", [((), []), (("w",), [f"z:w:{FID}"])])
async def test_mark_message_replaces_the_buttons_and_threads_a_silent_verdict(tg, configured, buttons, expected):
    ok = await R.TelegramStakeNotifier().mark_message(message_id=901, finding_id=FID,
                                                      verdict="⌛ Expired — not published", buttons=buttons)
    assert ok is True and tg.methods() == ["editMessageReplyMarkup", "sendMessage"]
    (markup,) = tg.of("editMessageReplyMarkup")
    assert markup["message_id"] == 901 and buttons_of(markup) == expected
    (reply,) = tg.of("sendMessage")
    assert reply["text"] == "⌛ Expired — not published" and reply["disable_notification"] is True
    assert reply["reply_parameters"] == {"message_id": 901, "allow_sending_without_reply": True}
    assert "reply_markup" not in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [
    {"message_id": None}, {"message_id": True}, {"message_id": "901"}, {"verdict": ""}, {"verdict": "  "},
    {"verdict": None}, {"buttons": ("x",)}, {"buttons": ("w", "w")}, {"buttons": "wk"},
    {"finding_id": "not-a-uuid", "buttons": ("w",)}, {"finding_id": None, "buttons": ("k",)},
])
async def test_mark_message_refuses_bad_input_without_a_request(tg, configured, kw):
    args = {"message_id": 901, "finding_id": FID, "verdict": "Expired", "buttons": ()} | kw
    assert await R.TelegramStakeNotifier().mark_message(**args) is False
    assert tg.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["editMessageReplyMarkup", "sendMessage"])
async def test_mark_message_is_false_when_either_half_fails(tg, configured, method):
    tg.script[method] = [refused("Bad Request: chat not found")]
    ok = await R.TelegramStakeNotifier().mark_message(message_id=901, finding_id=FID, verdict="Expired",
                                                      buttons=())
    assert ok is False and tg.methods() == ["editMessageReplyMarkup", "sendMessage"]   # the verdict still goes


@pytest.mark.asyncio
async def test_the_digest_is_silent_split_under_4096_headed_and_in_order(tg, configured):
    lines = [f"heads-up {i:03d}: Company {i} 8-K — https://www.sec.gov/Archives/edgar/data/{i}/x.htm"
             for i in range(300)]
    assert await R.TelegramStakeNotifier().send_digest(lines) is True
    parts = tg.of("sendMessage")
    assert len(parts) >= 3 and all(p["disable_notification"] is True for p in parts)
    assert all(R.utf16_len(p["text"]) <= 4096 and "reply_markup" not in p for p in parts)
    assert parts[0]["text"].startswith(R.DIGEST_HEADER)
    joined = "\n".join(p["text"] for p in parts).split("\n")[1:]
    assert joined == lines                                                          # every line, in order


@pytest.mark.asyncio
@pytest.mark.parametrize("lines", [[], [""], ["   "], [None, 3], ()])
async def test_an_empty_digest_sends_nothing_and_is_delivered(tg, configured, lines):
    assert await R.TelegramStakeNotifier().send_digest(lines) is True
    assert tg.calls == []


@pytest.mark.asyncio
async def test_a_digest_part_that_fails_stops_the_digest_and_is_false(tg, configured):
    tg.script["sendMessage"] = [None, (502, {"ok": False})]
    assert await R.TelegramStakeNotifier().send_digest(["x" * 900] * 20) is False
    assert len(tg.of("sendMessage")) == 2                                           # nothing after the failure


@pytest.mark.asyncio
async def test_the_digest_backstop_caps_the_parts_and_counts_what_it_left_out(tg, configured, monkeypatch, caplog):
    monkeypatch.setattr(R, "MAX_DIGEST_PARTS", 2)
    caplog.set_level(logging.ERROR, logger=R.__name__)
    lines = [f"line {i:04d} " + "y" * 80 for i in range(200)]
    # review A TG-2: dropped lines are NOT delivered — False, so the finder marks none reported
    assert await R.TelegramStakeNotifier().send_digest(lines) is False
    parts = tg.of("sendMessage")
    assert len(parts) == 2 and parts[-1]["text"].endswith("(see the logs).")
    shown = [line for p in parts for line in p["text"].split("\n") if line.startswith("line ")]
    left_out = int(parts[-1]["text"].split("… ")[1].split()[0])
    assert len(shown) + left_out == len(lines)
    assert any("NOT sent" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", [None, 5, {"a": 1}])
async def test_a_digest_that_is_not_a_list_is_false_and_sends_nothing(tg, configured, junk, caplog):
    caplog.set_level(logging.ERROR, logger=R.__name__)
    assert await R.TelegramStakeNotifier().send_digest(junk) is False
    assert tg.calls == [] and any("digest NOT SENT" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_digest_given_one_string_sends_it_as_one_line(tg, configured):
    assert await R.TelegramStakeNotifier().send_digest("just one line") is True
    (part,) = tg.of("sendMessage")
    assert part["text"].split("\n")[1:] == ["just one line"]


def test_digest_lines_keep_their_indent_and_lose_control_characters():
    parts, dropped = R.digest_parts(["  • indented", "tab\there\x07", "z" * 5000], today=date(2026, 10, 28))
    assert dropped == 0
    lines = "\n".join(parts).split("\n")
    assert lines[0] == f"{R.DIGEST_HEADER} · Wed Oct 28"
    assert lines[1] == "  • indented" and lines[2] == "tab here"
    assert len(lines[3]) == R.DIGEST_LINE_MAX and lines[3].endswith("…")


# ── the notifier: the flood gate, the pacer and failures ──────────────────────────────────────


async def _every_send(notifier: Any) -> List[Any]:
    return [
        await notifier.send_auto_note(finding(), stake_row()),
        await notifier.send_proposal(finding(), stake_row(), trial=False, evidence=[]),
        await notifier.send_lifecycle_note(finding(), stake_row(), text="t", offer_withdraw=True),
        await notifier.edit_proposal(finding(), stake_row(), message_id=9, trial=False, evidence=[]),
        await notifier.mark_message(message_id=9, finding_id=FID, verdict="v", buttons=()),
        await notifier.send_digest(["a line"]),
    ]


@pytest.mark.asyncio
async def test_a_429_closes_the_chat_for_every_sender_and_returns_none(tg, configured):
    tg.script["sendMessage"] = [too_many(45)]
    assert await R.TelegramStakeNotifier().send_auto_note(finding(), stake_row()) is None
    assert tp.is_rate_limited() and tp.seconds_left() > 40
    assert await R.TelegramStakeNotifier().send_proposal(finding(), stake_row(), trial=False, evidence=[]) is None
    assert len(tg.calls) == 1                                                       # the second never left


@pytest.mark.asyncio
async def test_a_closed_chat_sends_nothing_from_any_method(tg, configured, monkeypatch):
    """Checked BEFORE the pacer: a closed chat costs no turn on the shared lock (the pacer would refuse too —
    a pacer that does not is used here, so the check itself is what is proven)."""
    waits: List[str] = []

    class Lenient:
        async def wait(self) -> None:
            waits.append("wait")
    monkeypatch.setattr(tp, "PACER", Lenient())
    tp.note_rate_limit(30)
    assert await _every_send(R.TelegramStakeNotifier()) == [None, None, None, False, False, False]
    assert tg.calls == [] and waits == []


@pytest.mark.asyncio
async def test_not_configured_sends_nothing_and_is_not_available(tg, configured, monkeypatch, caplog):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", None)
    caplog.set_level(logging.ERROR, logger=R.__name__)
    notifier = R.TelegramStakeNotifier()
    assert notifier.available() is False
    assert await _every_send(notifier) == [None, None, None, False, False, False]
    assert tg.calls == [] and any("not configured" in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", SECRET)
    assert notifier.available() is True


def test_available_never_raises(monkeypatch):
    def boom() -> bool:
        raise RuntimeError("settings exploded")
    monkeypatch.setattr(rs, "is_configured", boom)
    assert R.TelegramStakeNotifier().available() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    refused("Bad Request: chat not found"), (502, {"ok": False}), httpx.ConnectError("down"),
    (200, {"ok": True, "result": {"chat": {"id": OWNER}}}),          # sent, but no message id came back
    (200, {"ok": True, "result": {"message_id": True}}),
])
async def test_a_failed_send_returns_none_and_logs_identifiers_never_the_row(tg, configured, caplog, answer):
    caplog.set_level(logging.WARNING, logger=R.__name__)
    tg.script["sendMessage"] = [answer]
    assert await R.TelegramStakeNotifier().send_auto_note(finding(), stake_row(investee_name="Secretive Corp")) is None
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert msgs and all(f"finding={FID}" in m and "slug=nvidia" in m and "accession=0001045810-26-000123" in m
                        and "reason=plain_13dg" in m for m in msgs)
    assert not any("Secretive" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("fid, ok", [(None, False), ("not-a-uuid", False), (12, False), (True, False),
                                     (FID.upper(), True), (uuid.UUID(FID), True)])
async def test_a_note_is_sent_only_with_buttons_that_can_be_decided(tg, configured, fid, ok):
    mid = await R.TelegramStakeNotifier().send_auto_note(finding(id=fid), stake_row())
    assert (mid is not None) is ok
    if ok:
        assert buttons_of(tg.of("sendMessage")[0]) == [f"z:w:{FID}", f"z:k:{FID}"]   # always canonical
    else:
        assert tg.calls == []


@pytest.mark.asyncio
async def test_a_note_that_cannot_be_rendered_is_not_sent_and_never_raises(tg, configured, monkeypatch, caplog):
    def boom(*_a: Any, **_k: Any) -> List[str]:
        raise KeyError("renderer bug")
    monkeypatch.setattr(R, "fact_lines", boom)
    caplog.set_level(logging.ERROR, logger=R.__name__)
    assert await R.TelegramStakeNotifier().send_auto_note(finding(), stake_row()) is None
    assert await R.TelegramStakeNotifier().edit_proposal(finding(), stake_row(), message_id=5, trial=False,
                                                         evidence=[]) is False
    assert tg.calls == [] and any("could not be built" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_every_send_and_edit_waits_its_turn_on_the_shared_pacer(tg, configured, monkeypatch):
    order: List[str] = []

    class Recorder:
        async def wait(self) -> None:
            order.append("wait")
    monkeypatch.setattr(tp, "PACER", Recorder())
    original = tg.handler

    def handler(request: httpx.Request) -> httpx.Response:
        order.append(request.url.path.rsplit("/", 1)[-1])
        return original(request)
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await _every_send(R.TelegramStakeNotifier())
    calls = [o for o in order if o != "wait"]
    assert len(calls) == 7                                  # 3 notes, 1 edit, mark = 2 calls, 1 digest part
    for k, item in enumerate(order):
        if item != "wait":
            assert k > 0 and order[k - 1] == "wait", order


@pytest.mark.asyncio
async def test_a_cancelled_send_propagates(tg, configured, monkeypatch):
    class Cancelled:
        async def wait(self) -> None:
            raise asyncio.CancelledError()
    monkeypatch.setattr(tp, "PACER", Cancelled())
    with pytest.raises(asyncio.CancelledError):
        await R.TelegramStakeNotifier().send_auto_note(finding(), stake_row())
    with pytest.raises(asyncio.CancelledError):
        await R.TelegramStakeNotifier().send_digest(["x"])


# ── pure helpers ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [
    (9.9, "9.9%"), (10.0, "10%"), (22.125, "22.12%"), (100, "100%"), (0.004, "0.004%"), (0, None), (-1, None),
    (True, None), ("9.9", None), (float("nan"), None), (float("inf"), None), (10 ** 400, None), (None, None),
])
def test_pct_text(value, expected):
    assert R.pct_text(value) == expected


@pytest.mark.parametrize("value, expected", [
    (2e9, "$2B ($2,000,000,000)"), (5.5e9, "$5.5B ($5,500,000,000)"), (1_234_567_890, "$1.23B ($1,234,567,890)"),
    (100e6, "$100M ($100,000,000)"), (750_000, "$750,000"), (3e12, "$3T ($3,000,000,000,000)"), (0, None),
    (-5, None), (False, None), ("5e9", None), (float("nan"), None), (10 ** 400, None),
])
def test_usd_text(value, expected):
    assert R.usd_text(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("2026-06-30", "Jun 30, 2026"), (date(2026, 1, 2), "Jan 2, 2026"), ("2026-10-28T22:05:00Z", "Oct 28, 2026"),
    (datetime(2026, 3, 4, tzinfo=UTC), "Mar 4, 2026"), ("2026-13-45", "2026-13-45"), ("soon\n", "soon"),
    ("", None), (None, None), (20260630, None), (True, None),
])
def test_date_text(value, expected):
    assert R.date_text(value) == expected


def test_keyboard_round_trips_fits_64_bytes_and_refuses_what_could_never_be_decided():
    board = R.keyboard(FID, ("w", "k", "p", "d"))
    datas = [b["callback_data"] for b in board["inline_keyboard"][0]]
    assert [sc.parse_callback_data(d) for d in datas] == [(v, FID) for v in "wkpd"]
    assert all(len(d.encode()) <= 64 for d in datas)
    assert R.keyboard(None, ()) == {"inline_keyboard": []}
    for verbs, fid in ((("x",), FID), (("w", "w"), FID), (("w",), None), (("w",), "abc"), (("w",), "")):
        with pytest.raises(ValueError):
            R.keyboard(fid, verbs)


@pytest.mark.parametrize("value, cap, expected", [
    ("a\nb\tc", 10, "a b c"), ("x" * 20, 10, "x" * 9 + "…"), ("a" + " " * 500 + "b", 10, "a…"),
    ("ok", 1, "o"), ("ok", 0, ""), (12, 5, "12"), ("‮evil​", 10, "evil"),
])
def test_one_line_cleans_caps_and_marks_what_it_cut(value, cap, expected):
    assert R._one_line(value, cap) == expected


def test_one_line_survives_a_hostile_str():
    class Hostile:
        def __str__(self) -> str:
            raise RuntimeError("no")
    assert R._one_line(Hostile(), 40) == "<unprintable Hostile>"


def test_reason_text_reads_any_shape(caplog):
    caplog.set_level(logging.WARNING, logger=R.__name__)
    assert R.reason_text("trial") == REASONS["trial"]
    assert R.reason_text("made_up\nline") == "reason made_up line"
    assert R.reason_text(None) == R.reason_text("") == R.reason_text(7) == "reason not recorded"
    assert any("not in stake_contracts.REASONS" in r.getMessage() for r in caplog.records)


def test_facts_survive_a_row_and_finding_of_the_wrong_type():
    text = R.auto_note_text(None, ["not", "a", "row"])  # type: ignore[arg-type]
    lines = text.split("\n")
    assert "a member → an unnamed company" in lines and "Stake: no figure on the row" in lines
    assert "Why: reason not recorded" in lines and "(no usable source link — see the logs)" in lines


# ── the taps: the seam in the marketing bot ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"from_id": OWNER + 1}, {"chat_id": OWNER + 1}, {"from_id": str(OWNER)},
                                {"chat_id": -100123}, {"from_id": None}])
async def test_a_strangers_z_tap_is_refused_before_the_handler(tg, configured, monkeypatch, kw):
    seen: List[Any] = []

    async def spy(**k: Any) -> bool:
        seen.append(k)
        return True
    monkeypatch.setattr(R, "handle_stake_callback", spy)
    assert await rs.handle_update(update(f"z:w:{FID}", **kw)) == {"ok": True, "refused": "not_allowed"}
    assert seen == [] and tg.answers() == ["Not allowed"]


@pytest.mark.asyncio
async def test_the_owners_z_tap_reaches_the_handler_with_the_tapped_message(tg, configured, monkeypatch):
    seen: List[Dict[str, Any]] = []

    async def spy(**k: Any) -> bool:
        seen.append(k)
        return True
    monkeypatch.setattr(R, "handle_stake_callback", spy)
    assert await rs.handle_update(update(f"z:p:{FID}")) == {"ok": True, "stakes": True, "decided": True}
    (call,) = seen
    assert call["data"] == f"z:p:{FID}" and call["from_id"] == OWNER and call["chat_id"] == OWNER
    assert call["message"]["message_id"] == DECISION_ID and call["cq_id"] == "cq-9"
    assert tg.calls == []                                   # the handler (here a spy) owns the answer


@pytest.mark.asyncio
async def test_the_dispatch_end_to_end_with_an_injected_finder(tg, configured, monkeypatch):
    spy = Spy(TapResult(ok=True, toast="Published ✅", verdict="✅ Published 18:05 ET", buttons=("w",)))
    monkeypatch.setitem(sys.modules, FINDER_MODULE, types.SimpleNamespace(apply_owner_decision=spy))
    assert await rs.handle_update(update(f"z:p:{FID}")) == {"ok": True, "stakes": True, "decided": True}
    assert spy.calls[0]["verb"] == "p" and spy.calls[0]["finding_id"] == FID
    assert spy.calls[0]["message_id"] == DECISION_ID
    assert tg.answers() == ["Published ✅"]


@pytest.mark.asyncio
async def test_a_stake_review_that_cannot_be_imported_is_answered_once_by_the_dispatcher(tg, configured,
                                                                                         monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "app.services.trillion_club.stake_review", None)
    out = await rs.handle_update(update(f"z:w:{FID}"))
    assert out == {"ok": True, "outcome": "error", "stakes": True}
    assert tg.answers() == ["Could not record that — tap again"]
    assert "the stake review tap FAILED" in caplog.text


@pytest.mark.asyncio
async def test_a_stake_handler_crash_is_answered_once_and_never_breaks_the_webhook(tg, configured, monkeypatch):
    async def boom(**_k: Any) -> bool:
        raise RuntimeError("handler exploded")
    monkeypatch.setattr(R, "handle_stake_callback", boom)
    assert await rs.handle_update(update(f"z:k:{FID}")) == {"ok": True, "outcome": "error", "stakes": True}
    assert tg.answers() == ["Could not record that — tap again"]


@pytest.mark.asyncio
async def test_marketing_and_learn_taps_never_reach_the_stake_handler(tg, configured, monkeypatch):
    seen: List[Any] = []

    async def spy(**k: Any) -> bool:
        seen.append(k)
        return True
    monkeypatch.setattr(R, "handle_stake_callback", spy)
    from app.services.learn_weekly import review as learn_review

    async def learn_spy(**_k: Any) -> bool:
        return False
    monkeypatch.setattr(learn_review, "handle_learn_callback", learn_spy)
    await rs.handle_update(update(f"x:{FID}"))
    await rs.handle_update(update(f"y:a:{FID}"))
    await rs.handle_update(update(f"Z:w:{FID}"))             # upper-case Z is not the stake prefix
    await rs.handle_update(update(f" z:w:{FID}"))
    assert seen == []


def test_the_toasts_and_labels_are_the_contracts_wording():
    assert (R.TOAST_UNKNOWN, R.TOAST_CRASH, R.TOAST_STILL_WORKING) == (
        "Unknown button", "Something went wrong — logged", "Still working — check again in a minute")
    assert "Trial — would auto-publish" in R.TRIAL_LINE
    assert set(R.BUTTON_LABELS) == set(sc.CALLBACK_VERBS)
    assert all(len(t) <= sc.TOAST_MAX_CHARS for t in (R.TOAST_UNKNOWN, R.TOAST_CRASH, R.TOAST_STILL_WORKING))
    assert set(R.KIND_LABELS) == set(STAKE_KINDS) and set(R.VALUE_BASIS_LABELS) == set(VALUE_BASES)


@pytest.mark.asyncio
async def test_a_tap_is_never_answered_twice(tg, configured, caplog):
    t = R._Tap(cq_id="cq", chat_id=OWNER, from_id=OWNER, message=message(), verb="w", finding_id=FID, now=NOW)
    caplog.set_level(logging.ERROR, logger=R.__name__)
    await R._answer(t, "first")
    await R._answer(t, "second")
    assert tg.answers() == ["first"]
    assert any("a second answer" in r.getMessage() for r in caplog.records)


def test_z_is_reserved_for_the_stake_finder_and_is_no_marketing_verb():
    assert "z" not in rs._VERBS and rs.STAKES_CALLBACK_PREFIX == sc.CALLBACK_PREFIX == "z:"
    assert rs.STAKES_CALLBACK_PREFIX != rs.LEARN_CALLBACK_PREFIX
    for verb in sc.CALLBACK_VERBS:
        assert rs.parse_callback_data(f"z:{verb}:{FID}") is None
        assert rs.parse_callback_data(sc.callback_data(verb, FID)) is None


# ── the taps: the handler ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tap_runs_the_decision_answers_once_and_appends_the_verdict(tg, configured):
    spy = Spy(TapResult(ok=True, toast="Published ✅", verdict="✅ Published 18:05 ET", buttons=("w",)))
    assert await tap(f"z:p:{FID}", apply=spy) is True
    assert spy.calls == [{"verb": "p", "finding_id": FID, "message_id": DECISION_ID, "now": NOW}]
    assert tg.answers() == ["Published ✅"]
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == DECISION_ID and edit["chat_id"] == OWNER
    assert edit["text"] == message()["text"] + "\n\n✅ Published 18:05 ET"
    assert buttons_of(edit) == [f"z:w:{FID}"] and "parse_mode" not in edit
    assert tg.methods() == ["answerCallbackQuery", "editMessageText"]               # answered first


@pytest.mark.asyncio
async def test_a_verdict_with_no_buttons_removes_the_keyboard(tg, configured):
    await tap(f"z:d:{FID}", apply=Spy(TapResult(ok=True, toast="Dismissed", verdict="🗑 Dismissed", buttons=())))
    (edit,) = tg.of("editMessageText")
    assert edit["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_no_verdict_means_no_edit_and_a_replay_is_answered_with_the_finders_toast(tg, configured):
    spy = Spy(TapResult(ok=False, toast="Already decided"))
    assert await tap(f"z:p:{FID}", apply=spy) is False
    assert await tap(f"z:p:{FID}", apply=spy) is False
    assert tg.answers() == ["Already decided", "Already decided"] and tg.of("editMessageText") == []
    assert len(spy.calls) == 2                               # each replay asks the finder; it decides nothing


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    None, 7, "", "z:", "z:w:", f"z:x:{FID}", f"z:W:{FID}", f"z:w:{FID.upper()}", f"z:w:{FID}\n", f"z:w:{FID} ",
    f"z:wk:{FID}", f"z:w:{FID[:-1]}", f"z:w:{FID}0", f"z:w:{FID.replace('-', '')}", "z:" + "w" * 100,
    f"y:a:{FID}", f"z:{FID}",
])
async def test_malformed_or_unknown_taps_are_answered_once_and_decide_nothing(tg, configured, data):
    spy = Spy()
    assert await tap(data, apply=spy) is False
    assert spy.calls == [] and tg.answers() == [R.TOAST_UNKNOWN] and tg.of("editMessageText") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [RuntimeError("finder exploded"), KeyError("x"), TimeoutError("its own"),
                                    {"ok": True}, None, "Published"])
async def test_a_crash_or_a_bad_answer_is_answered_once_and_logged(tg, configured, caplog, result):
    async def apply(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime) -> Any:
        if isinstance(result, BaseException):
            raise result
        return result
    caplog.set_level(logging.ERROR, logger=R.__name__)
    assert await tap(f"z:w:{FID}", apply=apply) is False
    assert tg.answers() == [R.TOAST_CRASH] and tg.of("editMessageText") == []
    records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert records and FID in records[0].getMessage()
    if isinstance(result, BaseException):
        assert records[0].exc_info is not None                 # the stack is captured


@pytest.mark.asyncio
async def test_a_decision_with_the_wrong_shape_is_a_crash(tg, configured):
    def sync_apply(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime) -> TapResult:
        return TapResult(ok=True, toast="sync")

    async def wrong_signature(verb: str) -> TapResult:
        return TapResult(ok=True, toast="never")
    assert await tap(f"z:w:{FID}", apply=sync_apply) is False
    assert await tap(f"z:w:{FID}", apply=wrong_signature) is False
    assert tg.answers() == [R.TOAST_CRASH, R.TOAST_CRASH]


@pytest.mark.asyncio
async def test_the_default_decision_is_the_finders_imported_lazily(tg, configured, monkeypatch):
    spy = Spy(TapResult(ok=True, toast="Kept 👍", verdict="👍 Kept 18:05 ET", buttons=("w",)))
    monkeypatch.setitem(sys.modules, FINDER_MODULE, types.SimpleNamespace(apply_owner_decision=spy))
    assert await tap(f"z:k:{FID}", apply=None) is True
    assert spy.calls[0]["verb"] == "k" and tg.answers() == ["Kept 👍"]


@pytest.mark.asyncio
async def test_a_finder_that_cannot_be_imported_is_answered_once(tg, configured, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, FINDER_MODULE, None)
    caplog.set_level(logging.ERROR, logger=R.__name__)
    assert await tap(f"z:w:{FID}", apply=None) is False
    assert tg.answers() == [R.TOAST_CRASH]
    assert any("FAILED" in r.getMessage() and r.exc_info for r in caplog.records)


def test_importing_the_review_loads_no_finder_code():
    """The lazy import is real: a fresh interpreter importing the review (and the marketing bot) never loads
    `stake_finder` — and the marketing bot alone never loads the review."""
    import subprocess
    from pathlib import Path
    code = ("import sys\nimport app.services.marketing.review_service\n"
            "print('REVIEW=' + str('app.services.trillion_club.stake_review' in sys.modules))\n"
            "import app.services.trillion_club.stake_review\n"
            f"print('FINDER=' + str({FINDER_MODULE!r} in sys.modules))\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]),
                         capture_output=True, text=True, timeout=120)
    assert "REVIEW=False" in out.stdout and "FINDER=False" in out.stdout, out.stderr[-2000:]


@pytest.mark.asyncio
async def test_a_slow_decision_is_answered_still_working_once_and_its_late_verdict_is_shown(tg, configured,
                                                                                            monkeypatch, caplog):
    monkeypatch.setattr(R, "TAP_TIMEOUT_SECONDS", 0.05)
    release = asyncio.Event()
    wrote: List[str] = []

    async def slow(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime) -> TapResult:
        await release.wait()
        wrote.append("published")                              # the write sequence ran to its end
        return TapResult(ok=True, toast="Published ✅", verdict="✅ Published 18:05 ET", buttons=("w",))
    caplog.set_level(logging.INFO, logger=R.__name__)
    assert await tap(f"z:p:{FID}", apply=slow) is False
    assert tg.answers() == [R.TOAST_STILL_WORKING] and tg.of("editMessageText") == []
    assert len(R._RUNNING) == 1                                 # the decision is still running, held strongly
    release.set()
    await _drain()
    assert wrote == ["published"] and tg.answers() == [R.TOAST_STILL_WORKING]   # never answered twice
    (edit,) = tg.of("editMessageText")
    assert edit["text"].endswith("\n\n✅ Published 18:05 ET") and buttons_of(edit) == [f"z:w:{FID}"]
    assert any("finished after its answer" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_slow_decision_that_fails_late_is_logged_with_its_stack(tg, configured, monkeypatch, caplog):
    monkeypatch.setattr(R, "TAP_TIMEOUT_SECONDS", 0.05)
    release = asyncio.Event()

    async def slow_boom(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime) -> TapResult:
        await release.wait()
        raise RuntimeError("late failure")
    caplog.set_level(logging.ERROR, logger=R.__name__)
    assert await tap(f"z:w:{FID}", apply=slow_boom) is False
    release.set()
    await _drain()
    late = [r for r in caplog.records if "FAILED after the owner was answered" in r.getMessage()]
    assert len(late) == 1 and late[0].exc_info is not None and FID in late[0].getMessage()
    assert tg.answers() == [R.TOAST_STILL_WORKING] and tg.of("editMessageText") == []
    # review A TG-3: the owner saw only "Still working" — the failure reaches them as a silent threaded reply
    (reply,) = tg.of("sendMessage")
    assert "tap again" in reply["text"] and reply["reply_parameters"]["message_id"] == DECISION_ID
    assert reply.get("disable_notification") is True


@pytest.mark.asyncio
async def test_a_slow_toast_only_result_reaches_the_owner_as_a_threaded_reply(tg, configured, monkeypatch):
    """Review A TG-3: a late refusal with no verdict ("Not published — the row now fails the stake
    validators") used to be logged only; the answer window had closed, so the owner never saw it."""
    monkeypatch.setattr(R, "TAP_TIMEOUT_SECONDS", 0.05)
    release = asyncio.Event()

    async def slow(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime) -> TapResult:
        await release.wait()
        return TapResult(ok=False, toast="Not published — the row now fails the stake validators")
    assert await tap(f"z:p:{FID}", apply=slow) is False
    release.set()
    await _drain()
    assert tg.answers() == [R.TOAST_STILL_WORKING] and tg.of("editMessageText") == []
    (reply,) = tg.of("sendMessage")
    assert reply["text"] == "Not published — the row now fails the stake validators"
    assert reply["reply_parameters"]["message_id"] == DECISION_ID and reply.get("disable_notification") is True


def test_the_tap_answers_well_inside_telegrams_callback_window():
    """A callback query answered after ~15 s is refused ('query is too old'): the "Still working" toast
    must go out well before that, and well inside the webhook's 45 s cap."""
    assert 0 < R.TAP_TIMEOUT_SECONDS <= 10


@pytest.mark.asyncio
async def test_a_decision_that_ends_as_the_webhook_is_cancelled_is_shown_exactly_once(tg, configured):
    """The decision finishes and the handler is cancelled in the same loop pass. Python 3.11's `wait_for`
    then either raises CancelledError (the `_detach` side reports) or returns the result (the handler shows
    it) — either way exactly ONE edit, never zero and never two."""
    started, release = asyncio.Event(), asyncio.Event()

    async def apply(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime) -> TapResult:
        started.set()
        await release.wait()
        return TapResult(ok=True, toast="Withdrawn", verdict="↩️ Withdrawn 18:05 ET", buttons=())
    handler = asyncio.ensure_future(tap(f"z:w:{FID}", apply=apply))
    await started.wait()
    release.set()
    await asyncio.sleep(0)            # the decision finishes; its callback runs; the handler has not resumed
    handler.cancel()
    try:
        await handler
        answered = ["Withdrawn"]
    except asyncio.CancelledError:
        answered = []
    await _drain()
    assert len(tg.of("editMessageText")) == 1 and tg.answers() == answered


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_first", [True, False])
async def test_a_detached_decision_is_reported_exactly_once_whichever_side_runs_second(tg, configured,
                                                                                      callback_first):
    """`_finished` (the done callback) and `_detach` (the handler giving up) race; the one that runs second
    reports. Driven directly, both orders."""
    t = R._Tap(cq_id="cq", chat_id=OWNER, from_id=OWNER, message=message(), verb="w", finding_id=FID, now=NOW)
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(TapResult(ok=True, toast="Withdrawn", verdict="↩️ Withdrawn", buttons=()))
    if callback_first:
        R._finished(t, fut)
        assert not R._RUNNING                                   # nothing reported yet: the handler still waits
        R._detach(t, fut)
    else:
        R._detach(t, fut)
        assert not R._RUNNING
        R._finished(t, fut)
    await _drain()
    assert len(tg.of("editMessageText")) == 1 and tg.answers() == []


@pytest.mark.asyncio
async def test_a_decision_the_handler_still_waits_for_is_never_reported_by_its_callback(tg, configured):
    t = R._Tap(cq_id="cq", chat_id=OWNER, from_id=OWNER, message=message(), verb="w", finding_id=FID, now=NOW)
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(TapResult(ok=True, toast="Withdrawn", verdict="↩️ Withdrawn", buttons=()))
    R._finished(t, fut)
    await _drain()
    assert tg.calls == []                                       # the handler shows it itself


@pytest.mark.asyncio
async def test_a_cancelled_webhook_never_cancels_the_decision(tg, configured):
    started, release = asyncio.Event(), asyncio.Event()
    wrote: List[str] = []

    async def apply(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime) -> TapResult:
        started.set()
        await release.wait()
        wrote.append("withdrawn")
        return TapResult(ok=True, toast="Withdrawn", verdict="↩️ Withdrawn 18:05 ET", buttons=())
    handler = asyncio.ensure_future(tap(f"z:w:{FID}", apply=apply))
    await started.wait()
    handler.cancel()                                     # the webhook's 45 s cap, or a shutdown
    with pytest.raises(asyncio.CancelledError):
        await handler
    assert wrote == []
    release.set()
    await _drain()
    assert wrote == ["withdrawn"]                        # shielded: the writes ran to their end
    assert tg.of("editMessageText")[0]["text"].endswith("↩️ Withdrawn 18:05 ET")


@pytest.mark.asyncio
async def test_the_tap_path_never_waits_on_the_pacer(tg, configured, monkeypatch):
    class Forbidden:
        async def wait(self) -> None:
            raise AssertionError("the tap path waited on the pacer")
    monkeypatch.setattr(tp, "PACER", Forbidden())
    tg.script["editMessageText"] = [refused("Bad Request: message to edit not found")]
    spy = Spy(TapResult(ok=True, toast="Published ✅", verdict="✅ Published", buttons=("w",)))
    assert await tap(f"z:p:{FID}", apply=spy) is True
    assert tg.methods() == ["answerCallbackQuery", "editMessageText", "sendMessage"]


@pytest.mark.asyncio
async def test_taps_work_with_the_finder_and_the_jobs_switched_off(tg, configured, monkeypatch):
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_FINDER_ENABLED", False)
    monkeypatch.setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", False)
    spy = Spy(TapResult(ok=True, toast="Withdrawn", verdict="↩️ Withdrawn", buttons=()))
    monkeypatch.setitem(sys.modules, FINDER_MODULE, types.SimpleNamespace(apply_owner_decision=spy))
    assert await rs.handle_update(update(f"z:w:{FID}")) == {"ok": True, "stakes": True, "decided": True}
    assert spy.calls and spy.calls[-1]["verb"] == "w"


@pytest.mark.asyncio
async def test_an_edit_that_fails_reoffers_the_buttons_on_a_new_threaded_message(tg, configured):
    tg.script["editMessageText"] = [refused("Bad Request: message to edit not found")]
    await tap(f"z:p:{FID}", apply=Spy(TapResult(ok=True, toast="Published", verdict="✅ Published",
                                                buttons=("w",))))
    (sent,) = tg.of("sendMessage")
    assert sent["text"] == "✅ Published" and buttons_of(sent) == [f"z:w:{FID}"]
    assert sent["reply_parameters"]["message_id"] == DECISION_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [refused("Bad Request: message to edit not found"), (502, {"ok": False})])
async def test_a_failed_edit_with_nothing_to_tap_sends_nothing_more(tg, configured, answer):
    tg.script["editMessageText"] = [answer]
    await tap(f"z:d:{FID}", apply=Spy(TapResult(ok=True, toast="Dismissed", verdict="🗑 Dismissed", buttons=())))
    assert tg.of("sendMessage") == []


@pytest.mark.asyncio
async def test_message_not_modified_is_a_replay_and_offers_nothing_new(tg, configured):
    tg.script["editMessageText"] = [refused("Bad Request: message is not modified")]
    await tap(f"z:p:{FID}", apply=Spy(TapResult(ok=True, toast="Published", verdict="✅ Published",
                                                buttons=("w",))))
    assert tg.of("sendMessage") == []


@pytest.mark.asyncio
async def test_an_edit_429_records_the_back_off_and_sends_nothing_more(tg, configured):
    tg.script["editMessageText"] = [too_many(20)]
    await tap(f"z:p:{FID}", apply=Spy(TapResult(ok=True, toast="Published", verdict="✅ Published",
                                                buttons=("w",))))
    assert tp.is_rate_limited() and tg.of("sendMessage") == []


@pytest.mark.asyncio
async def test_a_closed_chat_never_gets_the_reoffered_buttons(tg, configured):
    tg.script["editMessageText"] = [refused("Bad Request: message to edit not found")]
    tp.note_rate_limit(30)
    await tap(f"z:p:{FID}", apply=Spy(TapResult(ok=True, toast="Published", verdict="✅ Published",
                                                buttons=("w",))))
    assert tg.methods() == ["answerCallbackQuery", "editMessageText"]


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", [message(text=None), message(text=""), message(mid=None), {}])
async def test_a_tap_on_a_message_without_text_or_id_reoffers_buttons_instead_of_blanking_it(tg, configured, msg):
    await tap(f"z:p:{FID}", msg=msg, apply=Spy(TapResult(ok=True, toast="Published", verdict="✅ Published",
                                                         buttons=("w",))))
    assert tg.of("editMessageText") == []                  # never overwrites a message it cannot read
    (sent,) = tg.of("sendMessage")
    assert buttons_of(sent) == [f"z:w:{FID}"]


@pytest.mark.asyncio
async def test_a_failed_answer_never_stops_the_verdict(tg, configured):
    tg.script["answerCallbackQuery"] = [refused("Bad Request: query is too old")]
    assert await tap(f"z:k:{FID}", apply=Spy(TapResult(ok=True, toast="Kept", verdict="👍 Kept",
                                                       buttons=("w",)))) is True
    assert len(tg.of("answerCallbackQuery")) == 1 and len(tg.of("editMessageText")) == 1


@pytest.mark.asyncio
async def test_a_long_verdict_on_a_long_message_stays_within_telegrams_limit(tg, configured):
    long_message = message(text="n" * 4090)
    await tap(f"z:p:{FID}", msg=long_message, apply=Spy(TapResult(ok=True, toast="ok", verdict="v" * 2000,
                                                                  buttons=())))
    (edit,) = tg.of("editMessageText")
    assert R.utf16_len(edit["text"]) <= 4096 and edit["text"].endswith("…")


@pytest.mark.asyncio
@pytest.mark.parametrize("now", [None, datetime(2026, 10, 28, 18, 5), "2026-10-28"])
async def test_a_missing_or_naive_now_becomes_the_aware_clock(tg, configured, now):
    spy = Spy()
    await R.handle_stake_callback(cq_id="cq", chat_id=OWNER, from_id=OWNER, message=message(),
                                  data=f"z:w:{FID}", now=now, apply=spy)
    assert spy.calls[0]["now"].tzinfo is not None


@pytest.mark.asyncio
async def test_the_tapped_message_id_is_passed_only_when_it_is_one(tg, configured):
    spy = Spy()
    for mid in (True, "7001", 0, -3, None):
        await tap(f"z:k:{FID}", apply=spy, msg=message(mid=mid))
    assert [c["message_id"] for c in spy.calls] == [None] * 5
