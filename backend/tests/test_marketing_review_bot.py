"""
The Telegram REVIEW BOT (design doc §12.9): `app/integrations/telegram.py`,
`app/services/marketing/review_service.py`, the root webhook in `app/main.py` and the review sweep
inside the publisher loop.

Hermetic: Telegram is an `httpx.MockTransport` (`FakeTelegram`), the ledger is the in-memory
PostgREST fake from `test_marketing_run_service.py`, the webhook runs through `TestClient(app)`
WITHOUT the lifespan.

What must never regress:
  * the webhook gate — 401 without the secret header, 403 with a wrong one or with the secret
    unset on the server (fail-closed, ERROR once), 200 `{"ok": true}` for anything authenticated
    (Telegram replays a non-2xx forever), 413 over the body cap;
  * the owner allow-list — the tapping user AND the chat must both be the configured id, as real
    ints; anything else answers "Not allowed" and decides nothing;
  * the decision is `review_post`'s conditional UPDATE — a double tap / a retried update reports
    "Already …" and writes nothing;
  * the sweep: video first, then one message per post with the right buttons (on the LAST chunk
    only), a conditional stamp that never overwrites a decision, no re-notification, a 429 stops
    it, a 5xx is retried next cycle, a > 20 MB video becomes a link;
  * reject reasons (2026-10-01): only ❌ swaps the buttons for the reason keyboard (a new message when
    the edit fails); a reason tap records once (a repeat writes nothing, a later different reason
    wins), only on a rejected post, only from the owner;
  * the bot token (it is in the URL PATH) never appears in a log record or an exception message.

Mutation-tested by hand on 2026-09-29 (see the final report of the change): dropping the
`compare_digest` check in `_verify_telegram_webhook_secret`, and dropping the `chat_id == allowed`
half of the allow-list, each turned tests here red; restored.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.api.error_response import ErrorCode, classify_exception
from app.config import settings
from app.integrations import telegram
from app.log_redaction import SecretRedactingFilter, redact_secrets
from app.main import app
from app.services.marketing import publisher_service as pub
from app.services.marketing import review_service as rs
from app.services.marketing import run_service as mrs
from test_marketing_run_service import FakeSupabase, _col, _Query

OWNER = 424242
_BOT_ID = "123456789"
_BOT_SECRET = "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawZ"  # 35 chars, the real shape
TOKEN = f"{_BOT_ID}:{_BOT_SECRET}"
SECRET = "s3cret_Webhook-token"
PATH = "/marketing/telegram/webhook"
HDR = "X-Telegram-Bot-Api-Secret-Token"

RUN_A = "11111111-1111-4111-8111-111111111111"
RUN_B = "22222222-2222-4222-8222-222222222222"
VIDEO = "33333333-3333-4333-8333-333333333333"


# ── fakes ─────────────────────────────────────────────────────────────────────


class FakeTelegram:
    """Records every Bot API call; `script[method]` is a FIFO of canned answers:
    (status, json-or-bytes), an Exception to raise, or a callable(request, payload) returning one."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.urls: List[str] = []
        self.script: Dict[str, list] = {}
        self._next_id = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        self.urls.append(str(request.url))
        queue = self.script.get(method)
        if queue:
            item = queue.pop(0)
            if callable(item):
                item = item(request, payload)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, httpx.Response):
                return item
            if item is not None:
                status, body = item
                if isinstance(body, (bytes, str)):
                    return httpx.Response(status, content=body)
                return httpx.Response(status, json=body)
        self._next_id += 1
        if method in ("sendMessage", "sendVideo", "sendPhoto", "editMessageText"):
            return httpx.Response(200, json={"ok": True, "result": {
                "message_id": self._next_id, "chat": {"id": payload.get("chat_id")},
                "text": payload.get("text")}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def of(self, method: str) -> List[Dict[str, Any]]:
        return [p for m, p in self.calls if m == method]


def _is_with_json_path(self, col, val):
    """The fake's `is_` reads `r.get(col)` only; PostgREST also filters a `json->>key` path."""
    assert val == "null"
    neg, self._negate = self._negate, False
    self.filters.append(lambda r, c=col: (_col(r, c) is not None) if neg else (_col(r, c) is None))
    return self


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", OWNER)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "MARKETING_PUBLIC_BASE_URL", "https://caydexinvest.com")
    monkeypatch.setattr(rs, "SEND_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(rs, "_rate_limited_until", 0.0)
    monkeypatch.setattr(main_mod, "_telegram_secret_unset_logged", False)
    # Every platform publishes (and so gets Approve/Reject buttons) unless a test says otherwise —
    # the preview tests below narrow it.
    from app.schemas.marketing import POST_PLATFORMS
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: list(POST_PLATFORMS))
    # …and the web publisher can send (no "approving will not send it" warning) and every platform
    # can delete — the tests below that exercise those warnings narrow these again.
    monkeypatch.setattr(settings, "MARKETING_ENABLED", True)
    monkeypatch.setattr(settings, "MARKETING_DRY_RUN", False)
    monkeypatch.setattr(rs.outlets, "retract_capable", lambda _platform: True)


@pytest.fixture
def ledger(monkeypatch, configured):
    monkeypatch.setattr(_Query, "is_", _is_with_json_path)
    svc = mrs.MarketingRunService(supabase=FakeSupabase())
    svc.fake = svc._sb  # type: ignore[attr-defined]
    monkeypatch.setattr(rs, "get_marketing_run_service", lambda: svc)
    return svc


def _rows(svc, table):
    return svc.fake.tables[table].rows


def _seed_run(svc, run_id=RUN_A, run_date="2026-09-28", *, video_id: Optional[str] = VIDEO,
              video_bytes: Optional[int] = 5_000_000, dry_run=False):
    _rows(svc, mrs.RUNS).append({"id": run_id, "run_date": run_date, "status": "media_ready",
                                  "stage": "assets_ready", "attempts": 1, "dry_run": dry_run,
                                  "metadata": {"video_asset_id": video_id} if video_id else {}})
    if video_id:
        _rows(svc, mrs.ASSETS).append({
            "id": video_id, "run_id": run_id, "kind": "video", "status": "ready",
            "storage_path": f"{run_date}/video/{'a' * 64}.mp4", "content_type": "video/mp4",
            "bytes": video_bytes, "metadata": {}})


_SEQ = iter(range(10, 10**6))


def _seed_post(svc, run_id=RUN_A, platform="tiktok", fmt="video", *, caption="Hello caption",
               title: Optional[str] = "A title", asset_ids=None, meta=None, status="pending_review",
               post_id=None) -> str:
    pid = post_id or str(uuid.uuid4())
    n = next(_SEQ)
    ts = f"2026-09-28T10:{n // 60 % 60:02d}:{n % 60:02d}.000000+00:00"
    _rows(svc, mrs.POSTS).append({
        "id": pid, "run_id": run_id, "platform": platform, "format": fmt, "status": status,
        "title": title, "caption": caption, "asset_ids": list(asset_ids or []),
        "idempotency_key": f"2026-09-28:{platform}:{fmt}:{n}", "attempts": 0, "cost_micros": 0,
        "metadata": {"dry_run": False} if meta is None else meta,
        "created_at": ts, "updated_at": ts})
    return pid


def _post(svc, pid) -> Dict[str, Any]:
    return next(r for r in _rows(svc, mrs.POSTS) if r["id"] == pid)


# ── pure helpers ──────────────────────────────────────────────────────────────


_PID = "0f8fad5b-d9cb-469f-a165-70867728950e"


@pytest.mark.parametrize("data, expected", [
    (f"a:{_PID}", ("approve", _PID)),
    (f"r:{_PID}", ("reject", _PID)),
    (f"a:{_PID.upper()}", None),                 # uppercase uuid — not canonical
    (f"x:{_PID}", None),                         # unknown verb
    (f"approve:{_PID}", None),
    (f"a:{_PID}\n", None),                       # `$` would accept a trailing newline; fullmatch does not
    (f" a:{_PID}", None),
    (f"a:{_PID}extra", None),
    ("a:" + "0" * 62, None),                     # 64 bytes but not a uuid
    ("a:" + "f" * 200, None),                    # over 64 bytes
    (f"a:{_PID.replace('-', '')}", None),        # hex without dashes
    ("", None), (None, None), (123, None), (["a", _PID], None),
    (f"a:{_PID[:-1]}é", None),                   # non-ASCII
])
def test_parse_callback_data_is_strict(data, expected):
    assert rs.parse_callback_data(data) == expected


def test_callback_data_round_trips_and_fits_64_bytes():
    for decision in ("approve", "reject"):
        data = rs.callback_data(decision, _PID)
        assert len(data.encode()) <= 64
        assert rs.parse_callback_data(data) == (decision, _PID)
    with pytest.raises(ValueError):
        rs.callback_data("approve", _PID.upper())


def test_canonical_post_id():
    assert rs.canonical_post_id(_PID.upper()) == _PID
    assert rs.canonical_post_id(uuid.UUID(_PID)) == _PID
    for bad in (None, "", "not-a-uuid", 12, "a" * 36):
        assert rs.canonical_post_id(bad) is None


def test_split_message_respects_utf16_and_prefers_line_breaks():
    assert rs.split_message("short") == ["short"]
    assert rs.split_message("") == []
    assert rs.split_message("   \n ") == []
    exact = "x" * 4096
    assert rs.split_message(exact) == [exact]
    # One over: a hard cut, and a whitespace-only tail is dropped rather than sent empty.
    assert rs.split_message(exact + "\n") == [exact]
    lines = "\n".join(f"line {i} " + "y" * 90 for i in range(120))  # ~12 k chars
    chunks = rs.split_message(lines)
    assert len(chunks) >= 3 and all(rs.utf16_len(c) <= 4096 for c in chunks)
    assert all(not c.startswith("\n") for c in chunks)
    assert "\n".join(chunks) == lines  # broke on newlines, lost nothing
    # Astral-plane emoji are 2 UTF-16 units: 3000 of them is 6000 units, and no pair is split.
    emoji = "😀" * 3000
    chunks = rs.split_message(emoji)
    assert [rs.utf16_len(c) for c in chunks] == [4096, 1904] and "".join(chunks) == emoji
    # No separator anywhere: a hard cut on a character boundary.
    blob = "z" * 9000
    assert [len(c) for c in rs.split_message(blob)] == [4096, 4096, 808]


def test_append_verdict_stays_within_4096():
    assert rs.append_verdict("text", "✅ Approved 10:02 ET") == "text\n\n✅ Approved 10:02 ET"
    assert rs.append_verdict(None, "Post not found") == "Post not found"
    out = rs.append_verdict("😀" * 3000, "✅ Approved 10:02 ET")
    assert rs.utf16_len(out) <= 4096 and out.endswith("…\n\n✅ Approved 10:02 ET")


@pytest.mark.parametrize("meta, labelled", [
    ({"dry_run": True}, True), ({}, True), ({"dry_run": None}, True), ({"dry_run": "false"}, True),
    ({"dry_run": False}, False),
])
def test_dry_run_label_follows_the_publishers_rule(meta, labelled):
    text = rs.compose_post_text({"platform": "x", "format": "text", "caption": "c", "metadata": meta},
                                "2026-09-28", [])
    assert text.startswith("X · text · run 2026-09-28")
    assert ("· DRY RUN" in text.splitlines()[0]) is labelled


def test_is_configured_needs_all_three_real_values(monkeypatch, configured):
    assert rs.is_configured()
    for name, bad in [("MARKETING_TELEGRAM_BOT_TOKEN", None), ("MARKETING_TELEGRAM_BOT_TOKEN", ""),
                      ("MARKETING_TELEGRAM_WEBHOOK_SECRET", None),
                      ("MARKETING_TELEGRAM_REVIEW_CHAT_ID", None),
                      ("MARKETING_TELEGRAM_REVIEW_CHAT_ID", "424242"),
                      ("MARKETING_TELEGRAM_REVIEW_CHAT_ID", True),
                      # set but UNUSABLE: Telegram refuses the secret, or cannot reach an http URL —
                      # the buttons of every message sent could never work (review 2026-09-29)
                      ("MARKETING_TELEGRAM_WEBHOOK_SECRET", "b64+/secret=="),
                      ("MARKETING_TELEGRAM_WEBHOOK_SECRET", "x" * 257),
                      ("MARKETING_PUBLIC_BASE_URL", "http://caydexinvest.com"),
                      ("MARKETING_PUBLIC_BASE_URL", "")]:
        with monkeypatch.context() as m:
            m.setattr(settings, name, bad)
            assert not rs.is_configured(), (name, bad)


@pytest.mark.asyncio
async def test_a_set_but_unusable_secret_is_an_error_at_registration_not_silence(monkeypatch, configured, tg, caplog):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", "b64+/secret==")
    with caplog.at_level(logging.ERROR):
        assert await rs.register_webhook() is False
    assert any("WEBHOOK_SECRET" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert tg.of("setWebhook") == []
    assert not rs.is_configured()        # so the sweep sends no buttons that could never work


def test_settings_default_the_bot_off():
    from app.config import Settings

    f = Settings.model_fields
    assert f["MARKETING_TELEGRAM_BOT_TOKEN"].default is None
    assert f["MARKETING_TELEGRAM_REVIEW_CHAT_ID"].default is None
    assert f["MARKETING_TELEGRAM_WEBHOOK_SECRET"].default is None
    assert f["MARKETING_PUBLIC_BASE_URL"].default.startswith("https://")


# ── the thin client ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_sends_plain_text_with_the_preview_off(tg, configured):
    out = await telegram.send_message(OWNER, "*not markup*", reply_markup=rs.review_keyboard(_PID))
    assert out["ok"] is True and out["result"]["message_id"] > 100
    (payload,) = tg.of("sendMessage")
    assert "parse_mode" not in payload
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"a:{_PID}"
    assert tg.urls[0] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, exc, attrs", [
    ((429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 7",
            "parameters": {"retry_after": 7}}), telegram.TelegramRateLimitException, {"retry_after": 7}),
    ((429, {"ok": False, "parameters": {"retry_after": True}}), telegram.TelegramRateLimitException,
     {"retry_after": None}),
    ((502, b"<html>bad gateway</html>"), telegram.TelegramUnavailableException, {"status": 502}),
    ((400, {"ok": False, "description": "Bad Request: chat not found"}), telegram.TelegramRequestError,
     {"description": "Bad Request: chat not found"}),
    ((403, {"ok": False, "description": "Forbidden: bot was blocked by the user"}),
     telegram.TelegramRequestError, {"status": 403}),
    ((200, {"ok": False, "description": "odd"}), telegram.TelegramRequestError, {}),
    ((200, b"not json"), telegram.TelegramUnavailableException, {}),
    ((200, [1, 2]), telegram.TelegramUnavailableException, {}),
    ((302, b""), telegram.TelegramUnavailableException, {"status": 302}),
])
async def test_client_maps_every_failure_to_a_typed_exception(tg, configured, answer, exc, attrs):
    tg.script["sendMessage"] = [answer]
    with pytest.raises(exc) as info:
        await telegram.send_message(OWNER, "hi")
    for k, v in attrs.items():
        assert getattr(info.value, k) == v
    assert _BOT_SECRET not in str(info.value) and "api.telegram.org" not in str(info.value)
    assert info.value.method == "sendMessage"


@pytest.mark.asyncio
async def test_retry_after_falls_back_to_the_header(tg, configured):
    tg.script["sendMessage"] = [lambda req, p: httpx.Response(429, headers={"Retry-After": "12"},
                                                               json={"ok": False})]
    with pytest.raises(telegram.TelegramRateLimitException) as info:
        await telegram.send_message(OWNER, "hi")
    assert info.value.retry_after == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_errors_never_carry_the_token(tg, configured, error):
    # An httpx error whose message embeds the URL — exactly what must not escape.
    tg.script["sendMessage"] = [lambda req, p: error(f"boom while calling {req.url}", request=req)]
    with pytest.raises(telegram.TelegramUnavailableException) as info:
        await telegram.send_message(OWNER, "hi")
    assert _BOT_SECRET not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert error.__name__ in str(info.value)


@pytest.mark.asyncio
async def test_no_token_means_no_request(tg, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", None)
    with pytest.raises(telegram.TelegramNotConfiguredException):
        await telegram.send_message(OWNER, "hi")
    assert tg.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", ["", "has space", "a" * 257, "sëcret", "semi;colon", None])
async def test_set_webhook_refuses_a_secret_telegram_would_reject(tg, configured, secret):
    with pytest.raises(telegram.TelegramRequestError):
        await telegram.set_webhook("https://caydexinvest.com" + PATH, secret)
    assert tg.calls == []


def test_close_telegram_client_is_idempotent():
    asyncio.run(telegram.close_telegram_client())
    asyncio.run(telegram.close_telegram_client())
    assert telegram._client is None


@pytest.mark.parametrize("cls", [telegram.TelegramException, telegram.TelegramRateLimitException,
                                 telegram.TelegramUnavailableException, telegram.TelegramRequestError,
                                 telegram.TelegramNotConfiguredException])
def test_every_telegram_exception_classifies_to_the_review_bot_code(cls):
    # The messages carry exactly the words the generic heuristics key on ("429", "timed out").
    code, status = classify_exception(cls("telegram sendMessage: HTTP 429 rate limit timeout", method="m"))
    assert code == ErrorCode.MARKETING_REVIEW_BOT_UNAVAILABLE
    permanent = cls in (telegram.TelegramRequestError, telegram.TelegramNotConfiguredException)
    assert status == (502 if permanent else 503)


# ── redaction ─────────────────────────────────────────────────────────────────


def test_log_redaction_scrubs_a_bot_token_in_a_url_and_bare():
    url = f'HTTP Request: POST https://api.telegram.org/bot{TOKEN}/sendMessage "HTTP/1.1 200 OK"'
    out = redact_secrets(url)
    assert _BOT_SECRET not in out
    assert f"https://api.telegram.org/bot{_BOT_ID}:***/sendMessage" in out
    assert _BOT_SECRET not in redact_secrets(f"token was {TOKEN}.")
    assert _BOT_SECRET not in redact_secrets(json.dumps({"t": TOKEN}))
    # Never eats ordinary diagnostics.
    for keep in ("run 2026-09-28T10:02:03.123456+00:00", f"post_id={_PID}", "attempts=3 key=2026-09-28:x:text",
                 "12345:short", "sha256:" + "a" * 64, "1234:" + "b" * 40):
        assert redact_secrets(keep) == keep, keep


def test_the_root_filter_scrubs_a_token_record():
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "calling %s", (f"/bot{TOKEN}/getMe",), None)
    SecretRedactingFilter().filter(rec)
    assert _BOT_SECRET not in rec.getMessage()


# ── the webhook ───────────────────────────────────────────────────────────────


def _tap(post_id: str, verb: str = "a", *, from_id: Any = OWNER, chat_id: Any = OWNER, data: Any = "__default__",
         text: Any = "TIKTOK · video · run 2026-09-28\n\nHello caption", message_id: Any = 555,
         drop: tuple = ()) -> Dict[str, Any]:
    cq: Dict[str, Any] = {
        "id": "cbq-1", "from": {"id": from_id, "is_bot": False, "first_name": "Owner"},
        "message": {"message_id": message_id, "date": 1759000000,
                    "chat": {"id": chat_id, "type": "private"}, "text": text},
        "chat_instance": "ci", "data": f"{verb}:{post_id}" if data == "__default__" else data,
    }
    for key in drop:
        cq.pop(key, None)
    return {"update_id": 9001, "callback_query": cq}


@pytest.fixture
def client():
    # NOT `with TestClient(app)`: that would run the lifespan (Supabase, background loops).
    return TestClient(app)


def _post_hook(client, body, *, secret: Any = SECRET, raw: Optional[bytes] = None, headers=None):
    h: Dict[Any, Any] = dict(headers or {})
    if secret is not None:
        h[HDR] = secret
    if raw is not None:
        return client.post(PATH, content=raw, headers={**h, "Content-Type": "application/json"})
    return client.post(PATH, json=body, headers=h)


def test_missing_secret_header_is_401_auth_required(client, configured, tg):
    r = _post_hook(client, {"update_id": 1}, secret=None)
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_REQUIRED"
    assert tg.calls == []


@pytest.mark.parametrize("secret", ["wrong", SECRET + "x", SECRET[:-1], SECRET.upper()])
def test_wrong_secret_is_403(client, configured, tg, secret):
    r = _post_hook(client, _tap(_PID), secret=secret)
    assert r.status_code == 403 and r.json()["error_code"] == "AUTH_FORBIDDEN"
    assert tg.calls == []


def test_non_ascii_secret_is_a_mismatch_not_a_500(client, configured, tg):
    r = client.post(PATH, json=_tap(_PID), headers={HDR.encode(): "s\xe9cret".encode("latin-1")})
    assert r.status_code == 403 and r.json()["error_code"] == "AUTH_FORBIDDEN"


def test_unset_secret_on_the_server_is_403_and_errors_once(client, configured, tg, monkeypatch, caplog):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", None)
    caplog.set_level(logging.ERROR)
    for _ in range(2):
        r = _post_hook(client, _tap(_PID), secret="anything")
        assert r.status_code == 403 and r.json()["error_code"] == "AUTH_FORBIDDEN"
    errors = [rec for rec in caplog.records if "MARKETING_TELEGRAM_WEBHOOK_SECRET is not set" in rec.getMessage()]
    assert len(errors) == 1
    assert tg.calls == []


def test_approve_decides_the_post_answers_and_edits(client, ledger, tg):
    pid = _seed_post(ledger)
    r = _post_hook(client, _tap(pid, "a"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = _post(ledger, pid)
    assert row["status"] == "approved" and row["approved_by"] == f"telegram:{OWNER}"
    assert row["metadata"]["review"]["by"] == f"telegram:{OWNER}"
    (answer,) = tg.of("answerCallbackQuery")
    assert answer == {"callback_query_id": "cbq-1", "text": "Approved ✅"}
    (edit,) = tg.of("editMessageText")
    assert edit["chat_id"] == OWNER and edit["message_id"] == 555
    assert edit["reply_markup"] == {"inline_keyboard": []}
    assert "parse_mode" not in edit
    assert edit["text"].startswith("TIKTOK · video · run 2026-09-28\n\nHello caption\n\n")
    assert re.search(r"\n\n✅ Approved \d\d:\d\d ET$", edit["text"])


def test_reject(client, ledger, tg):
    pid = _seed_post(ledger)
    assert _post_hook(client, _tap(pid, "r")).status_code == 200
    row = _post(ledger, pid)
    assert row["status"] == "rejected" and row.get("approved_by") is None
    assert tg.of("answerCallbackQuery")[0]["text"] == "Rejected ❌"
    assert re.search(r"❌ Rejected \d\d:\d\d ET$", tg.of("editMessageText")[0]["text"])


def test_a_double_tap_reports_already_and_writes_nothing(client, ledger, tg):
    pid = _seed_post(ledger)
    _post_hook(client, _tap(pid, "a"))
    snapshot = dict(_post(ledger, pid))
    r = _post_hook(client, _tap(pid, "a"))  # a second tap, or Telegram retrying the update
    assert r.status_code == 200
    assert _post(ledger, pid) == snapshot
    assert tg.of("answerCallbackQuery")[1]["text"].startswith("Already approved")
    assert re.search(r"Already approved \(\d\d:\d\d ET\)$", tg.of("editMessageText")[1]["text"])


def test_approve_after_reject_cannot_flip_it(client, ledger, tg):
    pid = _seed_post(ledger)
    _post_hook(client, _tap(pid, "r"))
    _post_hook(client, _tap(pid, "a"))
    assert _post(ledger, pid)["status"] == "rejected"
    assert tg.of("answerCallbackQuery")[1]["text"].startswith("Already rejected")


def test_unknown_post_is_reported_not_found(client, ledger, tg):
    r = _post_hook(client, _tap(_PID, "a"))
    assert r.status_code == 200
    assert tg.of("answerCallbackQuery")[0]["text"] == "Post not found"
    assert tg.of("editMessageText")[0]["text"].endswith("\n\nPost not found")


@pytest.mark.parametrize("kw", [
    {"from_id": OWNER + 1},                       # another user in the owner's chat (a group)
    {"chat_id": OWNER + 1},                       # the owner, but in another chat
    {"chat_id": -100123},                         # a group / channel
    {"from_id": str(OWNER)},                      # right digits, wrong type
    {"chat_id": str(OWNER)},
    {"from_id": float(OWNER)},
    {"from_id": None},
    {"drop": ("from",)},
    {"drop": ("message",)},                       # an inline-mode tap carries no message
])
def test_the_allow_list_refuses_everyone_else(client, ledger, tg, kw):
    pid = _seed_post(ledger)
    r = _post_hook(client, _tap(pid, "a", **kw))
    assert r.status_code == 200
    assert _post(ledger, pid)["status"] == "pending_review"
    assert tg.of("answerCallbackQuery") == [{"callback_query_id": "cbq-1", "text": "Not allowed"}]
    assert tg.of("editMessageText") == []


def test_a_bool_id_never_passes_the_allow_list(client, ledger, tg, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", 1)
    pid = _seed_post(ledger)
    _post_hook(client, _tap(pid, "a", from_id=True, chat_id=True))
    assert _post(ledger, pid)["status"] == "pending_review"
    assert tg.of("answerCallbackQuery")[0]["text"] == "Not allowed"


@pytest.mark.parametrize("data", [
    "x:{pid}", "a:{PID}", "a:{pid}\n", "a;{pid}", "a:", "a", "", None, 7, "a:" + "f" * 100, "r:{pid} ",
])
def test_bad_callback_data_decides_nothing(client, ledger, tg, data):
    pid = _seed_post(ledger)
    if isinstance(data, str):
        data = data.format(pid=pid, PID=pid.upper())
    r = _post_hook(client, _tap(pid, data=data))
    assert r.status_code == 200
    assert _post(ledger, pid)["status"] == "pending_review"
    assert tg.of("answerCallbackQuery") == [{"callback_query_id": "cbq-1", "text": "Unknown action"}]


@pytest.mark.parametrize("update", [
    {"update_id": 1, "message": {"text": "hi", "chat": {"id": OWNER}}},
    {"update_id": 2},
    [1, 2, 3],
    "string",
    {"update_id": 3, "callback_query": "nope"},
    {"update_id": 4, "callback_query": {"from": {"id": OWNER}, "data": f"a:{_PID}"}},  # no id
])
def test_non_callback_updates_are_ignored_with_200(client, ledger, tg, update):
    r = _post_hook(client, update)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert tg.calls == []


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe\x00garbage", b"", b"[" * 50_000 + b"]" * 50_000])
def test_unreadable_bodies_are_acknowledged(client, configured, tg, raw, caplog):
    caplog.set_level(logging.WARNING)
    r = _post_hook(client, None, raw=raw)
    if len(raw) > rs.MAX_WEBHOOK_BODY_BYTES:
        assert r.status_code == 413
    else:
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert any("unreadable body" in rec.getMessage() for rec in caplog.records)
    assert tg.calls == []


def test_a_body_over_the_cap_is_413_after_auth(client, configured, tg):
    big = {"update_id": 1, "pad": "x" * (rs.MAX_WEBHOOK_BODY_BYTES + 10)}
    r = _post_hook(client, big)
    assert r.status_code == 413 and r.json()["error_code"] == "INVALID_INPUT"
    # …but an unauthenticated one is refused before the body is looked at.
    assert _post_hook(client, big, secret=None).status_code == 401


def test_a_handler_crash_still_answers_200(client, configured, monkeypatch, caplog):
    async def boom(update):
        raise RuntimeError("kaput")

    monkeypatch.setattr(rs, "handle_update", boom)
    caplog.set_level(logging.ERROR)
    r = _post_hook(client, _tap(_PID))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert any("update handling raised" in rec.getMessage() for rec in caplog.records)


def test_a_ledger_failure_keeps_the_buttons_and_asks_for_another_tap(client, ledger, tg, monkeypatch):
    pid = _seed_post(ledger)

    async def broken(*a, **k):
        raise mrs.MarketingRunError("review_post failed: 520")

    monkeypatch.setattr(ledger, "review_post", broken)
    r = _post_hook(client, _tap(pid))
    assert r.status_code == 200
    assert tg.of("answerCallbackQuery")[0]["text"] == "Could not record that — tap again"
    assert tg.of("editMessageText") == []


def test_telegram_failures_while_answering_never_undo_the_decision(client, ledger, tg):
    pid = _seed_post(ledger)
    tg.script["answerCallbackQuery"] = [(400, {"ok": False, "description": "Bad Request: query is too old"})]
    tg.script["editMessageText"] = [(502, b"")]
    assert _post_hook(client, _tap(pid)).status_code == 200
    assert _post(ledger, pid)["status"] == "approved"


def test_an_inaccessible_message_still_gets_the_verdict(client, ledger, tg):
    pid = _seed_post(ledger)
    _post_hook(client, _tap(pid, text=None))
    assert _post(ledger, pid)["status"] == "approved"
    assert re.fullmatch(r"✅ Approved \d\d:\d\d ET", tg.of("editMessageText")[0]["text"])


def test_a_tap_while_the_bot_is_half_configured_decides_nothing(client, ledger, tg, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", None)
    pid = _seed_post(ledger)
    assert _post_hook(client, _tap(pid)).status_code == 200
    assert _post(ledger, pid)["status"] == "pending_review"
    assert tg.calls == []


def test_the_webhook_route_is_post_only_and_outside_the_api():
    routes = {getattr(r, "path", None): getattr(r, "methods", set()) for r in app.routes}
    assert routes[PATH] == {"POST"}
    assert rs.WEBHOOK_PATH == PATH


# ── the sweep ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_sends_the_video_then_one_message_per_post_grouped_by_run(ledger, tg):
    _seed_run(ledger, RUN_A)
    _seed_run(ledger, RUN_B, "2026-09-29", video_id=None)
    p1 = _seed_post(ledger, RUN_A, "tiktok", "video", asset_ids=[VIDEO])
    p2 = _seed_post(ledger, RUN_B, "x", "text", title=None, caption="Run B words")
    p3 = _seed_post(ledger, RUN_A, "linkedin", "text", caption="Run A text")
    counters = await rs.review_cycle()
    assert counters == {"pending": 3, "sent": 3, "stamped": 3, "videos": 1, "failed": 0, "rate_limited": 0}
    methods = [m for m, _ in tg.calls]
    assert methods == ["sendVideo", "sendMessage", "sendMessage", "sendMessage"]
    video = tg.of("sendVideo")[0]
    assert video["chat_id"] == OWNER and video["video"].endswith(f"/2026-09-28/video/{'a' * 64}.mp4")
    assert video["video"].startswith(settings.SUPABASE_URL.rstrip("/") + "/storage/v1/object/public/")
    assert video["caption"].startswith("VIDEO · run 2026-09-28") and "DRY RUN" not in video["caption"]
    msgs = tg.of("sendMessage")
    # Run A's two posts (right after its video), then run B's.
    assert msgs[0]["text"].startswith("TIKTOK · video · run 2026-09-28\n\nA title\n\nHello caption")
    assert "Media:\nvideo: https://" in msgs[0]["text"]
    assert msgs[1]["text"].startswith("LINKEDIN · text · run 2026-09-28")
    assert msgs[2]["text"] == "X · text · run 2026-09-29\n\nRun B words"
    for msg, pid in zip(msgs, (p1, p3, p2)):
        assert msg["reply_markup"] == {"inline_keyboard": [
            [{"text": "✅ Approve", "callback_data": f"a:{pid}"}],
            [{"text": "❌ Reject", "callback_data": f"r:{pid}"}]]}
        assert "parse_mode" not in msg
    for pid in (p1, p2, p3):
        meta = _post(ledger, pid)["metadata"]
        assert meta["review_notified_at"] and isinstance(meta["review_message_id"], int)
        assert meta["dry_run"] is False  # the stamp keeps the rest of the metadata
        assert _post(ledger, pid)["status"] == "pending_review"


@pytest.mark.asyncio
async def test_a_second_sweep_notifies_nothing(ledger, tg):
    _seed_run(ledger)
    _seed_post(ledger, asset_ids=[VIDEO])
    await rs.review_cycle()
    tg.calls.clear()
    counters = await rs.review_cycle()
    assert counters["pending"] == 0 and tg.calls == []


@pytest.mark.asyncio
async def test_an_undecided_backlog_never_starves_a_new_post(ledger, tg):
    """The not-notified filter runs IN the query, before its LIMIT: 60 older posts that were
    announced but not decided yet must not hide a new one."""
    _seed_run(ledger, video_id=None)
    for _ in range(60):
        _seed_post(ledger, platform="x", fmt="text",
                   meta={"dry_run": False, "review_notified_at": "2026-09-27T10:00:00+00:00"})
    fresh = _seed_post(ledger, platform="x", fmt="text", caption="the new one")
    counters = await rs.review_cycle()
    assert counters["pending"] == 1 and counters["sent"] == 1
    assert f"a:{fresh}" in json.dumps(tg.of("sendMessage")[0]["reply_markup"])


@pytest.mark.asyncio
async def test_a_long_caption_is_split_with_the_buttons_on_the_last_chunk_only(ledger, tg):
    _seed_run(ledger, video_id=None)
    caption = "\n".join(f"Paragraph {i}. " + "word " * 40 for i in range(60))  # ~13 k chars
    pid = _seed_post(ledger, platform="linkedin", fmt="text", caption=caption)
    await rs.review_cycle()
    msgs = tg.of("sendMessage")
    assert len(msgs) >= 3
    assert all(rs.utf16_len(m["text"]) <= 4096 for m in msgs)
    assert [("reply_markup" in m) for m in msgs] == [False] * (len(msgs) - 1) + [True]
    assert f"a:{pid}" in json.dumps(msgs[-1]["reply_markup"])
    joined = "\n".join(m["text"] for m in msgs)
    assert caption.replace("\n", "").replace(" ", "") in joined.replace("\n", "").replace(" ", "")
    meta = _post(ledger, pid)["metadata"]
    assert len(meta["review_message_ids"]) == len(msgs) and meta["review_message_id"] == meta["review_message_ids"][-1]


@pytest.mark.asyncio
async def test_a_post_decided_while_it_was_being_sent_is_not_stamped(ledger, tg):
    _seed_run(ledger, video_id=None)
    pid = _seed_post(ledger, platform="x", fmt="text")

    def decide_meanwhile(request, payload):
        row = _post(ledger, pid)
        row.update({"status": "approved", "approved_by": "telegram:1",
                    "metadata": {**row["metadata"], "review": {"decision": "approved"}},
                    "updated_at": "2026-09-28T11:00:00.000000+00:00"})
        return None  # then answer normally

    tg.script["sendMessage"] = [decide_meanwhile]
    counters = await rs.review_cycle()
    assert counters["sent"] == 1 and counters["stamped"] == 0
    row = _post(ledger, pid)
    assert row["status"] == "approved" and "review_notified_at" not in row["metadata"]
    assert row["metadata"]["review"] == {"decision": "approved"}


@pytest.mark.asyncio
async def test_the_stamp_is_fenced_on_updated_at(ledger, tg, monkeypatch):
    """A write between the stamp's read and its UPDATE (still pending) loses the stamp instead of
    clobbering the other writer's metadata — the post is re-sent next cycle (at-least-once)."""
    _seed_run(ledger, video_id=None)
    pid = _seed_post(ledger, platform="x", fmt="text")
    real_get = ledger.get_post

    async def stale_read(post_id):
        row = await real_get(post_id)
        _post(ledger, pid).update({"metadata": {"dry_run": False, "other": 1},
                                   "updated_at": "2026-09-28T12:00:00.000000+00:00"})
        return row

    monkeypatch.setattr(ledger, "get_post", stale_read)
    counters = await rs.review_cycle()
    assert counters["sent"] == 1 and counters["stamped"] == 0
    assert _post(ledger, pid)["metadata"] == {"dry_run": False, "other": 1}


@pytest.mark.asyncio
async def test_a_stamp_failure_is_logged_and_the_post_is_resent(ledger, tg, monkeypatch, caplog):
    _seed_run(ledger, video_id=None)
    pid = _seed_post(ledger, platform="x", fmt="text")

    real_get = ledger.get_post
    failures = [mrs.MarketingRunError("get_post failed: 520")]

    async def broken_once(post_id):
        if failures:
            raise failures.pop()
        return await real_get(post_id)

    monkeypatch.setattr(ledger, "get_post", broken_once)
    caplog.set_level(logging.WARNING)
    first = await rs.review_cycle()
    assert first["sent"] == 1 and first["stamped"] == 0
    assert any("SENT but the notified stamp failed" in r.getMessage() for r in caplog.records)
    assert "review_notified_at" not in _post(ledger, pid)["metadata"]
    counters = await rs.review_cycle()
    assert counters["sent"] == 1 and counters["stamped"] == 1
    assert len(tg.of("sendMessage")) == 2  # at least once: sent again


@pytest.mark.asyncio
async def test_a_429_stops_the_sweep_and_holds_it_off(ledger, tg, caplog):
    _seed_run(ledger, video_id=None)
    pids = [_seed_post(ledger, platform=p, fmt="text") for p in ("x", "threads", "bluesky")]
    tg.script["sendMessage"] = [(429, {"ok": False, "description": "Too Many Requests: retry after 30",
                                       "parameters": {"retry_after": 30}})]
    caplog.set_level(logging.WARNING)
    counters = await rs.review_cycle()
    assert counters["rate_limited"] == 1 and counters["sent"] == 0
    assert len(tg.of("sendMessage")) == 1
    assert any("retry_after=30" in r.getMessage() for r in caplog.records)
    assert all("review_notified_at" not in _post(ledger, p)["metadata"] for p in pids)
    # Flood control still in effect: the next cycle does not call Telegram at all.
    tg.calls.clear()
    assert (await rs.review_cycle())["rate_limited"] == 1 and tg.calls == []


@pytest.mark.asyncio
async def test_a_5xx_is_counted_and_retried_next_cycle(ledger, tg):
    _seed_run(ledger, video_id=None)
    p1 = _seed_post(ledger, platform="x", fmt="text", caption="first")
    p2 = _seed_post(ledger, platform="threads", fmt="text", caption="second")
    tg.script["sendMessage"] = [(502, b"bad gateway")]
    counters = await rs.review_cycle()
    assert counters["failed"] == 1 and counters["sent"] == 1 and counters["stamped"] == 1
    assert "review_notified_at" not in _post(ledger, p1)["metadata"]
    assert _post(ledger, p2)["metadata"]["review_notified_at"]
    tg.calls.clear()
    counters = await rs.review_cycle()
    assert counters == {"pending": 1, "sent": 1, "stamped": 1, "videos": 0, "failed": 0, "rate_limited": 0}
    assert tg.of("sendMessage")[0]["text"].endswith("first")


@pytest.mark.asyncio
async def test_a_video_over_20_mb_is_sent_as_a_link(ledger, tg):
    _seed_run(ledger, video_bytes=25 * 1024 * 1024)
    _seed_post(ledger, asset_ids=[VIDEO])
    counters = await rs.review_cycle()
    assert tg.of("sendVideo") == []
    link, post = tg.of("sendMessage")
    assert link["text"].startswith("VIDEO · run 2026-09-28") and ".mp4" in link["text"]
    assert "reply_markup" not in link and "reply_markup" in post
    assert counters["videos"] == 1 and counters["sent"] == 1


@pytest.mark.asyncio
async def test_a_refused_video_url_falls_back_to_a_link(ledger, tg):
    _seed_run(ledger)
    _seed_post(ledger, asset_ids=[VIDEO])
    tg.script["sendVideo"] = [(400, {"ok": False, "description": "Bad Request: failed to get HTTP URL content"})]
    counters = await rs.review_cycle()
    assert [m for m, _ in tg.calls] == ["sendVideo", "sendMessage", "sendMessage"]
    assert tg.of("sendMessage")[0]["text"].startswith("VIDEO · run")
    assert counters["sent"] == 1


@pytest.mark.asyncio
async def test_an_ambiguous_video_failure_sends_the_link_and_lets_the_posts_through(ledger, tg):
    """A timeout or a 5xx on sendVideo may come AFTER Telegram delivered the video. Holding the
    posts re-sent that video every cycle while its posts never went out (review 2026-09-29): the
    link goes out instead, then the posts, and nothing is re-sent next cycle."""
    _seed_run(ledger)
    pid = _seed_post(ledger, asset_ids=[VIDEO])
    tg.script["sendVideo"] = [(503, b"")]
    counters = await rs.review_cycle()
    assert counters["sent"] == 1 and counters["failed"] == 0
    link, post = tg.of("sendMessage")
    assert "/marketing-media/" in link["text"] and "reply_markup" in post
    assert _post(ledger, pid)["metadata"]["review_notified_at"]
    again = await rs.review_cycle()
    assert again["sent"] == 0 and len(tg.of("sendVideo")) == 1   # never re-sent


@pytest.mark.asyncio
async def test_a_video_outage_that_also_loses_the_link_holds_the_posts(ledger, tg):
    """When neither the video nor its link went out, the reviewer has not seen the media: the
    run's posts wait for the next cycle (and are not stamped)."""
    _seed_run(ledger)
    pid = _seed_post(ledger, asset_ids=[VIDEO])
    tg.script["sendVideo"] = [(503, b"")]
    tg.script["sendMessage"] = [(502, b"")]
    counters = await rs.review_cycle()
    assert counters["failed"] == 1 and counters["sent"] == 0
    assert "review_notified_at" not in _post(ledger, pid)["metadata"]
    counters = await rs.review_cycle()
    assert counters["videos"] == 1 and counters["sent"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_patch", [{"status": "pending_upload"}, {"kind": "audio"},
                                         {"run_id": RUN_B}, {"storage_path": None}])
async def test_only_a_ready_video_of_this_run_is_shown(ledger, tg, asset_patch):
    _seed_run(ledger)
    _rows(ledger, mrs.ASSETS)[0].update(asset_patch)
    _seed_post(ledger, asset_ids=[VIDEO])
    await rs.review_cycle()
    assert tg.of("sendVideo") == [] and len(tg.of("sendMessage")) == 1


@pytest.mark.asyncio
async def test_the_dry_run_label_reaches_the_message(ledger, tg):
    _seed_run(ledger, dry_run=True)
    _seed_post(ledger, asset_ids=[VIDEO], meta={"dry_run": True})
    await rs.review_cycle()
    assert "· DRY RUN" in tg.of("sendVideo")[0]["caption"]
    assert tg.of("sendMessage")[0]["text"].splitlines()[0] == "TIKTOK · video · run 2026-09-28 · DRY RUN"


@pytest.mark.asyncio
async def test_a_missing_run_row_or_an_unreadable_id_degrades_per_post(ledger, tg):
    good = _seed_post(ledger, RUN_B, "x", "text")  # RUN_B has no row: header falls back to the key
    bad = _seed_post(ledger, RUN_B, "threads", "text", post_id="not-a-uuid")
    counters = await rs.review_cycle()
    assert counters["sent"] == 1 and counters["failed"] == 1
    assert tg.of("sendMessage")[0]["text"].startswith("X · text · run 2026-09-28")
    assert _post(ledger, good)["metadata"]["review_notified_at"]
    assert "review_notified_at" not in _post(ledger, bad)["metadata"]


@pytest.mark.asyncio
async def test_a_malformed_row_costs_only_its_own_run(ledger, tg, caplog):
    _seed_run(ledger, RUN_A)
    _seed_run(ledger, RUN_B, "2026-09-29", video_id=None)
    bad = _seed_post(ledger, RUN_A, "tiktok", "video", asset_ids=None)
    _post(ledger, bad)["asset_ids"] = 5  # not a list: iterating it raises TypeError
    good = _seed_post(ledger, RUN_B, "x", "text")
    caplog.set_level(logging.ERROR)
    counters = await rs.review_cycle()
    assert counters["failed"] == 1 and counters["sent"] == 1
    assert _post(ledger, good)["metadata"]["review_notified_at"]
    assert any(f"run_id={RUN_A} could not be reviewed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_ledger_outage_is_logged_never_raised(ledger, tg, monkeypatch, caplog):
    async def broken(*a, **k):
        raise mrs.MarketingRunError("list failed: 520")

    monkeypatch.setattr(rs, "_pending_unnotified", broken)
    caplog.set_level(logging.ERROR)
    assert (await rs.review_cycle())["failed"] == 1
    assert any("could not read pending posts" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_an_unconfigured_bot_sends_nothing(ledger, tg, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", None)
    _seed_post(ledger, platform="x", fmt="text")
    assert (await rs.review_cycle())["pending"] == 0 and tg.calls == []


@pytest.mark.asyncio
async def test_the_sweep_spaces_its_sends(ledger, tg, monkeypatch):
    _seed_run(ledger, video_id=None)
    for p in ("x", "threads", "bluesky"):
        _seed_post(ledger, platform=p, fmt="text")
    slept: List[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(rs, "SEND_SPACING_SECONDS", 1.1)
    monkeypatch.setattr(rs.asyncio, "sleep", fake_sleep)
    await rs.review_cycle()
    assert len(slept) == 2 and all(0 < s <= 1.1 for s in slept)


# ── the token never leaks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_bot_token_never_reaches_a_log_record_or_an_exception(ledger, tg, caplog):
    _seed_run(ledger)
    _seed_post(ledger, asset_ids=[VIDEO])
    _seed_post(ledger, platform="x", fmt="text")
    _seed_post(ledger, platform="threads", fmt="text")
    # Cycle 1: the video send dies in the transport with the URL in the error text, and the
    # fallback link is refused (400), so the run's posts are held. Cycle 2: everything goes.
    tg.script["sendVideo"] = [lambda req, p: httpx.ConnectError(f"cannot reach {req.url}", request=req)]
    tg.script["sendMessage"] = [(400, {"ok": False, "description": "Bad Request: chat not found"})]
    caplog.set_level(logging.DEBUG)
    # Explicitly: the worker's `marketing.main` raises the process-global httpx logger to WARNING
    # when another test imports it, and the web process logs httpx at INFO.
    caplog.set_level(logging.INFO, logger="httpx")
    first = await rs.review_cycle()
    second = await rs.review_cycle()
    assert first["failed"] == 3 and second["failed"] == 0 and second["sent"] == 3
    rendered = [r.getMessage() for r in caplog.records]
    rendered += [logging.Formatter().formatException(r.exc_info) for r in caplog.records if r.exc_info]
    assert rendered, "sentinel: nothing was logged"
    assert not [m for m in rendered if _BOT_SECRET in m], "the bot token reached a log record"
    # Anti-vacuity: httpx DID log the request URL — redacted on the httpx logger itself.
    assert any(f"api.telegram.org/bot{_BOT_ID}:***/" in m for m in rendered)


# ── the publisher loop runs the sweep ─────────────────────────────────────────


#: The steps that run LAST in the tick (the 2026-10-01 measurement build): step → (module, function).
#: They are built beside this file; `_late_steps()` finds the ones that exist, and from then on every
#: assertion below holds them to the contract (order and gates) — strictly, all 16 switch combinations.
_LATE_STEPS = {"measure": ("metrics_service", "measure_cycle"), "health": ("digest_service", "health_cycle"),
               "digest": ("digest_service", "digest_cycle")}


def _late_steps() -> Dict[str, Any]:
    """{step: module} for each late step whose module exists."""
    import importlib

    out: Dict[str, Any] = {}
    for step, (mod_name, _fn) in _LATE_STEPS.items():
        dotted = f"app.services.marketing.{mod_name}"
        try:
            out[step] = importlib.import_module(dotted)
        except ModuleNotFoundError as e:
            if e.name != dotted:
                raise   # the module exists but one of ITS imports is missing: loud, never "not built"
    return out


def _expected_tick(present, *, enabled: bool, bot: bool, metrics: bool, digest: bool) -> List[str]:
    """The tick's contract: expire → retract always; [MARKETING_ENABLED] reconcile → publish;
    [bot configured] review → feed; then LAST, so they never delay a post or a review message:
    [MARKETING_ENABLED and MARKETING_METRICS_ENABLED] measure, [bot configured] health,
    [bot configured and MARKETING_DIGEST_ENABLED] digest."""
    order = ["expire", "retract"]
    if enabled:
        order += ["reconcile", "publish"]
    if bot:
        order += ["review", "feed"]
    if "measure" in present and enabled and metrics:
        order.append("measure")
    if "health" in present and bot:
        order.append("health")
    if "digest" in present and bot and digest:
        order.append("digest")
    return order


def _stub_steps(monkeypatch, calls, *, fail=()):
    """Every step of the publisher tick replaced by a recorder (a name in `fail` raises) — the late
    steps too, so no test here ever reaches a real ledger read or a platform call."""
    def make(name, result):
        async def step(*_a, **_k):
            calls.append(name)
            if name in fail:
                raise RuntimeError(f"{name} exploded")
            return result
        return step

    monkeypatch.setattr(pub, "_expire_step", make("expire", {"expired": 0, "runs_closed": 0}))
    monkeypatch.setattr(pub, "retract_cycle", make("retract", {"retracted": 0}))
    monkeypatch.setattr(pub, "reconcile_cycle", make("reconcile", {"checked": 0}))
    monkeypatch.setattr(pub, "publish_cycle", make("publish", {"approved_waiting": 0, "published": 0}))
    monkeypatch.setattr(pub.review_service, "review_cycle",
                        make("review", {"pending": 0, "sent": 0, "failed": 0, "rate_limited": 0}))
    monkeypatch.setattr(pub.publish_feed, "feed_cycle", make("feed", {"posted": 0}))
    for step, module in _late_steps().items():
        fn = _LATE_STEPS[step][1]
        monkeypatch.setattr(module, fn, make(step, {}))
        if hasattr(pub, fn):    # a name bound into publisher_service itself
            monkeypatch.setattr(pub, fn, make(step, {}))


def _set_switches(monkeypatch, present, *, enabled: bool, bot: bool, metrics: bool, digest: bool) -> None:
    monkeypatch.setattr(pub.review_service, "is_configured", lambda: bot)
    monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", enabled)
    if "measure" in present:
        monkeypatch.setattr(pub.settings, "MARKETING_METRICS_ENABLED", metrics)
    if "digest" in present:
        monkeypatch.setattr(pub.settings, "MARKETING_DIGEST_ENABLED", digest)


@pytest.mark.asyncio
async def test_the_tick_order_and_its_switches(monkeypatch):
    """expire → retract always; reconcile → publish only with MARKETING_ENABLED; the Telegram
    review sweep and feed only when the bot is configured — and publishing BEFORE Telegram, so a
    slow Telegram never delays a post. The measure / health / digest steps run LAST behind their own
    gates (`_expected_tick`). Every combination of the four switches."""
    import itertools

    calls: List[str] = []
    _stub_steps(monkeypatch, calls)
    present = _late_steps()
    for enabled, bot, metrics, digest in itertools.product((False, True), repeat=4):
        _set_switches(monkeypatch, present, enabled=enabled, bot=bot, metrics=metrics, digest=digest)
        calls.clear()
        await pub.publisher_tick()
        assert calls == _expected_tick(present, enabled=enabled, bot=bot, metrics=metrics, digest=digest), \
            dict(enabled=enabled, bot=bot, metrics=metrics, digest=digest)


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["expire", "retract", "reconcile", "publish", "review", "feed",
                                     *_LATE_STEPS])
async def test_a_failing_step_never_stops_the_others(monkeypatch, caplog, failing):
    present = _late_steps()
    if failing in _LATE_STEPS and failing not in present:
        pytest.skip(f"the {failing} step's module is not built yet")
    calls: List[str] = []
    _stub_steps(monkeypatch, calls, fail=(failing,))
    _set_switches(monkeypatch, present, enabled=True, bot=True, metrics=True, digest=True)
    caplog.set_level(logging.ERROR)
    await pub.publisher_tick()
    assert calls == _expected_tick(present, enabled=True, bot=True, metrics=True, digest=True)
    assert any(f"marketing publisher step {failing} failed" in r.getMessage() and r.exc_info
               for r in caplog.records)


@pytest.mark.asyncio
async def test_the_loop_keeps_running_across_ticks(monkeypatch):
    ticks = []
    waits = []

    async def tick():
        ticks.append(1)
        if len(ticks) == 3:
            raise asyncio.CancelledError

    async def no_sleep(_s):
        return None

    async def no_wait(timeout):
        waits.append(timeout)
        return False

    monkeypatch.setattr(pub, "publisher_tick", tick)
    monkeypatch.setattr(pub.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(pub.publisher_wake, "wait", no_wait)
    monkeypatch.setattr(pub.settings, "MARKETING_PUBLISHER_INTERVAL_SECONDS", 600)
    with pytest.raises(asyncio.CancelledError):
        await pub.run_marketing_publisher_loop()
    assert len(ticks) == 3 and waits == [600, 600]


@pytest.mark.asyncio
async def test_a_wake_cuts_the_wait_short_and_is_consumed():
    from app.services.marketing import publisher_wake
    assert await publisher_wake.wait(0.01) is False          # nothing pending: times out
    publisher_wake.wake()
    assert await asyncio.wait_for(publisher_wake.wait(30), timeout=1) is True
    assert await publisher_wake.wait(0.01) is False          # one wake → one early tick


# ── startup registration ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_webhook_sets_the_secret_and_callback_only(tg, configured):
    assert await rs.register_webhook() is True
    (payload,) = tg.of("setWebhook")
    assert payload == {"url": "https://caydexinvest.com/marketing/telegram/webhook", "secret_token": SECRET,
                       "allowed_updates": ["callback_query"], "drop_pending_updates": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("patch, calls_telegram", [
    ({"MARKETING_PUBLIC_BASE_URL": "http://caydexinvest.com"}, False),
    ({"MARKETING_PUBLIC_BASE_URL": ""}, False),
    ({"MARKETING_TELEGRAM_WEBHOOK_SECRET": "bad secret!"}, False),
    ({"MARKETING_TELEGRAM_BOT_TOKEN": None}, False),
])
async def test_register_webhook_refuses_bad_configuration_without_calling(tg, configured, monkeypatch,
                                                                          patch, calls_telegram):
    for k, v in patch.items():
        monkeypatch.setattr(settings, k, v)
    assert await rs.register_webhook() is False
    assert bool(tg.calls) is calls_telegram


@pytest.mark.asyncio
async def test_register_webhook_failure_is_a_warning_not_a_crash(tg, configured, caplog):
    tg.script["setWebhook"] = [(500, b"")]
    caplog.set_level(logging.WARNING)
    assert await rs.register_webhook() is False
    assert any("setWebhook FAILED" in r.getMessage() for r in caplog.records)


def test_the_lifespan_registers_the_webhook_once_in_production_only():
    from pathlib import Path

    from test_marketing_smart_link import _production_spawns

    src = (Path(main_mod.__file__)).read_text(encoding="utf-8")
    spawns = _production_spawns(src)
    assert spawns.get("marketing_telegram_webhook") == "review_service.register_webhook()"
    assert "marketing_telegram_webhook" in main_mod._ONE_SHOT_TASKS  # a one-shot, not a dead loop
    assert spawns.get("marketing_publisher") == "run_marketing_publisher_loop()"


# ══ Phase 5 (design doc §12.10): new verbs, previews, retract / unknown-outcome taps ══════════
#
# Imports for this section only (kept here so the Phase-4 block above stays as it was).
import copy  # noqa: E402

from app.integrations import bluesky as bluesky_api  # noqa: E402
from app.integrations import x_api  # noqa: E402
from app.services.marketing import outlets  # noqa: E402

#: The REAL predicate, captured before any fixture replaces it (the `configured` fixture enables
#: every platform; the preview tests narrow it, one test uses the real thing).
_REAL_ENABLED_PLATFORMS = outlets.enabled_platforms

_ALL_VERBS = [("a", "approve"), ("r", "reject"), ("d", "retract"), ("k", "confirm_retract"),
              ("c", "cancel_retract"), ("l", "live"), ("n", "not_posted"),
              # the reject-reason keyboard (2026-10-01)
              ("t", "reason_tone"), ("f", "reason_accuracy"), ("p", "reason_compliance"),
              ("w", "reason_weak"), ("o", "reason_other")]
_X_ID = "1840000000000000001"
_X_URL = f"https://x.com/i/web/status/{_X_ID}"
_BSKY_URI = "at://did:plc:abcdefghijklmnopqrstuvwx/app.bsky.feed.post/3l6oveex3ii2l"
_BSKY_URL = "https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx/post/3l6oveex3ii2l"


@pytest.mark.parametrize("verb, decision", _ALL_VERBS)
def test_every_verb_parses_and_round_trips_within_64_bytes(verb, decision):
    data = rs.callback_data(decision, _PID)
    assert data == f"{verb}:{_PID}"
    assert len(data.encode("utf-8")) <= 64
    assert rs.parse_callback_data(data) == (decision, _PID)
    with pytest.raises(ValueError):
        rs.callback_data(decision, _PID.upper())   # only a canonical uuid makes a button


@pytest.mark.parametrize("data", [
    f"x:{_PID}", f"X:{_PID}",                                   # never an `x` verb, any case
    f"D:{_PID}", f"K:{_PID}", f"C:{_PID}", f"L:{_PID}", f"N:{_PID}",
    f"approve:{_PID}", f"retract:{_PID}", f"live:{_PID}",
    f"d:{_PID.upper()}", f"k:{_PID.upper()}", f"c:{_PID.upper()}", f"l:{_PID.upper()}", f"n:{_PID.upper()}",
    f"dk:{_PID}", f"kd:{_PID}", f"d:k:{_PID}",
    f"b:{_PID}", f"e:{_PID}", f"-:{_PID}", f"]:{_PID}", f"^:{_PID}",   # not verbs (and not regex holes)
    # the reason verbs: single lowercase letters only, never their names, never two letters
    f"T:{_PID}", f"F:{_PID}", f"P:{_PID}", f"W:{_PID}", f"O:{_PID}", f"t:{_PID.upper()}",
    f"tone:{_PID}", f"reason_tone:{_PID}", f"tf:{_PID}", f"to:{_PID}", f"t:t:{_PID}", f"t:{_PID}\n", "w:",
    f"d:{_PID}\n", f"k:{_PID} ", f" n:{_PID}", f"l;{_PID}", f"c:{_PID[:-1]}",
    "d:", "k", "d:" + "f" * 100,
])
def test_the_new_verbs_are_just_as_strict(data):
    assert rs.parse_callback_data(data) is None


@pytest.mark.parametrize("build, decisions", [
    (rs.review_keyboard, ["approve", "reject"]),
    (rs.retract_keyboard, ["retract"]),
    (rs.confirm_retract_keyboard, ["confirm_retract", "cancel_retract"]),
    (rs.unknown_outcome_keyboard, ["live", "not_posted"]),
    (rs.reject_reason_keyboard, ["reason_tone", "reason_accuracy", "reason_compliance", "reason_weak",
                                 "reason_other"]),
])
def test_every_keyboard_button_round_trips_to_its_post(build, decisions):
    datas = [b["callback_data"] for row in build(_PID)["inline_keyboard"] for b in row]
    assert [rs.parse_callback_data(d) for d in datas] == [(dec, _PID) for dec in decisions]
    assert all(len(d.encode("utf-8")) <= 64 for d in datas)
    with pytest.raises(ValueError):
        build("not-a-uuid")


# ── previews: a platform that cannot publish gets no buttons ──────────────────


def _set_created(svc, pid: str, ts: str) -> None:
    _post(svc, pid).update({"created_at": ts, "updated_at": ts})


@pytest.mark.asyncio
async def test_unwired_platforms_get_a_read_only_preview_stamped_apart(ledger, tg, monkeypatch):
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: [])
    _seed_run(ledger, video_id=None)
    pids = {p: _seed_post(ledger, platform=p, fmt="text", title=None, caption=f"{p} words")
            for p in ("x", "bluesky", "threads")}
    counters = await rs.review_cycle()
    assert counters == {"pending": 3, "sent": 3, "stamped": 3, "videos": 0, "failed": 0, "rate_limited": 0}
    msgs = tg.of("sendMessage")
    assert len(msgs) == 3
    for msg, p in zip(msgs, pids):
        assert "reply_markup" not in msg
        assert msg["text"] == f"{p.upper()} · text · run 2026-09-28 · preview — {p} not wired yet\n\n{p} words"
        assert "parse_mode" not in msg
    for pid in pids.values():
        row = _post(ledger, pid)
        assert row["status"] == "pending_review"
        assert row["metadata"]["review_preview_at"] and "review_notified_at" not in row["metadata"]
        assert isinstance(row["metadata"]["review_message_id"], int)
        assert row["metadata"]["dry_run"] is False
    tg.calls.clear()
    assert (await rs.review_cycle())["pending"] == 0 and tg.calls == []   # a preview is sent once


@pytest.mark.asyncio
async def test_a_preview_says_dry_run_before_preview(ledger, tg, monkeypatch):
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: [])
    _seed_run(ledger, video_id=None)
    _seed_post(ledger, platform="x", fmt="text", meta={"dry_run": True})
    await rs.review_cycle()
    assert tg.of("sendMessage")[0]["text"].splitlines()[0] == \
        "X · text · run 2026-09-28 · DRY RUN · preview — x not wired yet"


@pytest.mark.asyncio
async def test_a_previewed_post_gets_its_buttons_once_its_platform_is_enabled(ledger, tg, monkeypatch):
    enabled: List[str] = []
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: list(enabled))
    _seed_run(ledger, video_id=None)
    x = _seed_post(ledger, platform="x", fmt="text", title=None, caption="x words")
    b = _seed_post(ledger, platform="bluesky", fmt="text", title=None, caption="b words")
    await rs.review_cycle()
    previewed_at = _post(ledger, x)["metadata"]["review_preview_at"]
    preview_mid = _post(ledger, x)["metadata"]["review_message_id"]
    tg.calls.clear()

    enabled.append("x")
    counters = await rs.review_cycle()
    assert counters["sent"] == 1 and counters["stamped"] == 1
    (msg,) = tg.of("sendMessage")
    assert msg["text"] == "X · text · run 2026-09-28\n\nx words"
    assert msg["reply_markup"] == rs.review_keyboard(x)
    meta_x, meta_b = _post(ledger, x)["metadata"], _post(ledger, b)["metadata"]
    assert meta_x["review_notified_at"] and meta_x["review_preview_at"] == previewed_at
    # The Approve message is the one a later "Posted" reply threads under, not the preview.
    assert isinstance(meta_x["review_message_id"], int) and meta_x["review_message_id"] != preview_mid
    assert "review_notified_at" not in meta_b and meta_b["review_preview_at"]
    tg.calls.clear()
    assert (await rs.review_cycle())["pending"] == 0 and tg.calls == []


@pytest.mark.asyncio
async def test_a_post_sent_with_buttons_is_never_re_sent_as_a_preview(ledger, tg, monkeypatch):
    enabled = ["x"]
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: list(enabled))
    _seed_run(ledger, video_id=None)
    x = _seed_post(ledger, platform="x", fmt="text")
    await rs.review_cycle()
    enabled.clear()                                     # X switched off after the buttons went out
    tg.calls.clear()
    assert (await rs.review_cycle())["pending"] == 0 and tg.calls == []
    assert "review_preview_at" not in _post(ledger, x)["metadata"]


@pytest.mark.asyncio
async def test_the_real_enabled_predicate_decides_preview_vs_buttons(ledger, tg, monkeypatch):
    """Listed AND configured: Bluesky (both) gets buttons; X is listed but has no credentials;
    Threads is not listed — both of those are previews."""
    monkeypatch.setattr(rs.outlets, "enabled_platforms", _REAL_ENABLED_PLATFORMS)
    monkeypatch.setattr(settings, "MARKETING_PUBLISH_PLATFORMS", " X ,bluesky,bluesky")
    for name in ("MARKETING_X_CONSUMER_KEY", "MARKETING_X_CONSUMER_SECRET", "MARKETING_X_ACCESS_TOKEN",
                 "MARKETING_X_ACCESS_TOKEN_SECRET"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "MARKETING_X_MONTHLY_BUDGET_USD", 2.0)
    monkeypatch.setattr(settings, "MARKETING_BLUESKY_HANDLE", "caydex.bsky.social")
    monkeypatch.setattr(settings, "MARKETING_BLUESKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop")
    assert outlets.enabled_platforms() == ["bluesky"]
    _seed_run(ledger, video_id=None)
    pids = {p: _seed_post(ledger, platform=p, fmt="text") for p in ("x", "bluesky", "threads")}
    await rs.review_cycle()
    by_header = {m["text"].split(" · ", 1)[0]: m for m in tg.of("sendMessage")}
    assert by_header["BLUESKY"]["reply_markup"] == rs.review_keyboard(pids["bluesky"])
    assert "preview" not in by_header["BLUESKY"]["text"].splitlines()[0]
    for p in ("x", "threads"):
        assert "reply_markup" not in by_header[p.upper()]
        assert by_header[p.upper()]["text"].splitlines()[0].endswith(f"· preview — {p} not wired yet")
        assert _post(ledger, pids[p])["metadata"]["review_preview_at"]
    assert _post(ledger, pids["bluesky"])["metadata"]["review_notified_at"]


@pytest.mark.asyncio
async def test_previews_never_starve_the_button_query(ledger, tg, monkeypatch):
    """60 OLDER pending posts of an unwired platform must not keep a NEW post of an enabled
    platform from getting its Approve buttons in the same sweep — the two in-query reads exist
    exactly so neither kind can starve the other."""
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: ["x"])
    _seed_run(ledger, video_id=None)
    for i in range(60):
        _set_created(ledger, _seed_post(ledger, platform="bluesky", fmt="text"),
                     f"2026-09-28T09:{i // 60:02d}:{i % 60:02d}.000000+00:00")
    wired = _seed_post(ledger, platform="x", fmt="text", caption="the wired one")
    _set_created(ledger, wired, "2026-09-28T11:00:00.000000+00:00")
    await rs.review_cycle()
    assert _post(ledger, wired)["metadata"].get("review_notified_at")
    assert any(m.get("reply_markup") == rs.review_keyboard(wired) for m in tg.of("sendMessage"))


@pytest.mark.asyncio
async def test_an_older_wired_post_is_sent_alongside_a_preview_backlog(ledger, tg, monkeypatch):
    """The non-starving half that holds today: a wired post OLDER than the preview backlog."""
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: ["x"])
    _seed_run(ledger, video_id=None)
    wired = _seed_post(ledger, platform="x", fmt="text")
    _set_created(ledger, wired, "2026-09-28T08:00:00.000000+00:00")
    for i in range(60):
        _set_created(ledger, _seed_post(ledger, platform="bluesky", fmt="text"),
                     f"2026-09-28T09:00:{i:02d}.000000+00:00")
    counters = await rs.review_cycle()
    assert counters["sent"] == rs.SCAN_LIMIT
    assert tg.of("sendMessage")[0]["reply_markup"] == rs.review_keyboard(wired)
    assert _post(ledger, wired)["metadata"]["review_notified_at"]


# ── retract and unknown-outcome taps through the real webhook route ───────────


@pytest.fixture
def wakes(monkeypatch):
    calls: List[int] = []
    monkeypatch.setattr(rs.publisher_wake, "wake", lambda: calls.append(1))
    return calls


@pytest.fixture
def platform_apis(monkeypatch):
    """X and Bluesky clients that RECORD every request: the webhook must never call a platform
    (rules/marketing.md §2 — only the publisher loop does)."""
    seen: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return httpx.Response(500, json={"error": "the webhook must not call a platform"})

    monkeypatch.setattr(x_api, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(bluesky_api, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # REAL credentials-shaped settings, so a call from the webhook would actually reach the recording
    # transport — with none set, x_api/bluesky raise NotConfigured BEFORE the transport and this
    # guard passed whatever the webhook did (review 2026-09-30, mutation-checked).
    for name, value in (("MARKETING_X_CONSUMER_KEY", "ck_test_consumer_key_0001"),
                        ("MARKETING_X_CONSUMER_SECRET", "cs_test_consumer_secret_0002"),
                        ("MARKETING_X_ACCESS_TOKEN", "1234567890-at_test_access_token_0003"),
                        ("MARKETING_X_ACCESS_TOKEN_SECRET", "ats_test_access_secret_0004"),
                        ("MARKETING_X_MONTHLY_BUDGET_USD", 2.0),
                        ("MARKETING_BLUESKY_HANDLE", "caydex-test.bsky.social"),
                        ("MARKETING_BLUESKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop"),
                        ("MARKETING_BLUESKY_SERVICE", "https://bsky.social")):
        monkeypatch.setattr(settings, name, value)
    from app.services.marketing import outlet_bluesky, outlets
    outlet_bluesky._reset_state()   # no cached session / back-off can short-circuit before the transport
    # And the adapter layer itself: any send / reconcile / retract from the webhook is recorded too.
    for platform, adapter in list(outlets.ADAPTERS.items()):
        for method in ("send", "reconcile", "retract"):
            async def _recorded(*_a, _p=platform, _m=method, **_k):
                seen.append(f"adapter {_p}.{_m}")
                raise AssertionError(f"the webhook called {_p}.{_m}")
            monkeypatch.setattr(adapter, method, _recorded)
    yield seen
    outlet_bluesky._reset_state()


def _seed_published(svc, platform: str = "x", *, status: str = "published",
                    meta_extra: Optional[Dict[str, Any]] = None, external: bool = True) -> str:
    meta: Dict[str, Any] = {
        "dry_run": False, "review_notified_at": "2026-09-28T10:00:00+00:00", "review_message_id": 500,
        "review": {"decision": "approved", "by": f"telegram:{OWNER}", "at": "2026-09-28T10:05:00+00:00"},
        "publish": {"state": "published", "attempt": 1},
        "posted_notified_at": "2026-09-28T12:01:00+00:00", "posted_message_id": 555,
    }
    meta.update(meta_extra or {})
    pid = _seed_post(svc, platform=platform, fmt="text", title=None, status=status, meta=meta)
    if external:
        ext = (_X_ID, _X_URL) if platform == "x" else (_BSKY_URI, _BSKY_URL)
        _post(svc, pid).update({"external_id": ext[0], "external_url": ext[1], "attempts": 1,
                                "published_at": "2026-09-28T12:00:00+00:00"})
    return pid


def _snap(svc, pid) -> Dict[str, Any]:
    return copy.deepcopy(_post(svc, pid))


def _answers(tg) -> List[str]:
    return [a.get("text") for a in tg.of("answerCallbackQuery")]


@pytest.mark.parametrize("platform", ["x", "bluesky"])
def test_retract_asks_for_confirmation_and_writes_nothing(client, ledger, tg, wakes, platform_apis, platform):
    pid = _seed_published(ledger, platform)
    before = _snap(ledger, pid)
    r = _post_hook(client, _tap(pid, "d"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert tg.of("editMessageReplyMarkup") == [{"chat_id": OWNER, "message_id": 555,
                                                "reply_markup": rs.confirm_retract_keyboard(pid)}]
    assert _answers(tg) == ["Confirm the delete"]
    assert tg.of("editMessageText") == [] and tg.of("sendMessage") == []
    assert _post(ledger, pid) == before
    assert wakes == [] and platform_apis == []


@pytest.mark.parametrize("how", ["edit_refused", "no_message_id"])
def test_retract_offers_the_confirmation_on_a_new_message_when_it_cannot_edit(client, ledger, tg, wakes, how):
    pid = _seed_published(ledger)
    before = _snap(ledger, pid)
    if how == "edit_refused":
        tg.script["editMessageReplyMarkup"] = [(400, {"ok": False, "description": "Bad Request: message can't be edited"})]
        _post_hook(client, _tap(pid, "d"))
        assert len(tg.of("editMessageReplyMarkup")) == 1
    else:
        _post_hook(client, _tap(pid, "d", message_id=None))
        assert tg.of("editMessageReplyMarkup") == []
    (msg,) = tg.of("sendMessage")
    assert msg["text"] == f"Delete the X post? {_X_URL}"
    assert msg["reply_markup"] == rs.confirm_retract_keyboard(pid)
    assert "parse_mode" not in msg
    assert _post(ledger, pid) == before and wakes == []


@pytest.mark.parametrize("platform", ["x", "bluesky"])
def test_confirm_records_the_request_wakes_the_publisher_and_calls_no_platform(client, ledger, tg, wakes,
                                                                               platform_apis, platform):
    pid = _seed_published(ledger, platform)
    before = _snap(ledger, pid)
    r = _post_hook(client, _tap(pid, "k", text="✅ Posted on X · run 2026-09-28\nhttps://x.com/i/web/status/1"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = _post(ledger, pid)
    assert row["status"] == "published"
    meta = row["metadata"]
    assert meta["retract_requested_at"]
    assert meta["retract"] == {"requested_at": meta["retract_requested_at"], "by": f"telegram:{OWNER}",
                               "attempts": 0, "state": "requested"}
    for key, value in before["metadata"].items():          # the request MERGES
        assert meta[key] == value, key
    for col in ("external_id", "external_url", "cost_micros", "attempts", "published_at"):
        assert row[col] == before[col], col
    assert wakes == [1]
    assert platform_apis == [], "the webhook called a platform"
    assert _answers(tg) == ["Deleting…"]
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == 555 and edit["reply_markup"] == {"inline_keyboard": []}
    assert re.fullmatch(r"✅ Posted on X · run 2026-09-28\nhttps://x\.com/i/web/status/1\n\n"
                        r"🗑 Retract requested \d\d:\d\d ET", edit["text"])


def test_a_second_confirm_is_already_being_deleted_and_writes_nothing(client, ledger, tg, wakes, platform_apis):
    pid = _seed_published(ledger)
    _post_hook(client, _tap(pid, "k"))
    after_first = _snap(ledger, pid)
    _post_hook(client, _tap(pid, "k"))         # a second tap, or Telegram replaying the update
    assert _post(ledger, pid) == after_first
    assert _answers(tg) == ["Deleting…", "Already being deleted"]
    assert tg.of("editMessageText")[1]["text"].endswith("\n\nAlready being deleted")
    assert wakes == [1] and platform_apis == []
    # …and a fresh Retract tap on the old keyboard says so instead of asking again.
    _post_hook(client, _tap(pid, "d"))
    assert _answers(tg)[-1] == "Nothing to do — the post retract already requested"
    assert tg.of("editMessageReplyMarkup") == []


def test_a_closed_retract_may_be_requested_again(client, ledger, tg, wakes):
    """A request that ENDED without a delete (gave up / by hand — `retract_closed_at`) can be made
    again once the owner fixed what made it fail; the new request clears the closed marker."""
    pid = _seed_published(ledger, meta_extra={
        "retract_requested_at": "2026-09-28T13:00:00+00:00", "retract_closed_at": "2026-09-28T13:20:00+00:00",
        "retract": {"requested_at": "2026-09-28T13:00:00+00:00", "by": f"telegram:{OWNER}", "attempts": 3,
                    "state": "gave_up", "error": "x delete_post: HTTP 403"},
        "alert_kind": "retract_failed", "alert_notified_at": "2026-09-28T13:21:00+00:00"})
    _post_hook(client, _tap(pid, "d"))
    assert _answers(tg) == ["Confirm the delete"] and len(tg.of("editMessageReplyMarkup")) == 1
    _post_hook(client, _tap(pid, "k"))
    meta = _post(ledger, pid)["metadata"]
    assert "retract_closed_at" not in meta
    assert meta["retract"]["state"] == "requested" and meta["retract"]["attempts"] == 0
    assert meta["retract_requested_at"] != "2026-09-28T13:00:00+00:00"
    assert _answers(tg)[-1] == "Deleting…" and wakes == [1]


def test_cancel_restores_the_retract_button_and_writes_nothing(client, ledger, tg, wakes, platform_apis):
    pid = _seed_published(ledger)
    before = _snap(ledger, pid)
    _post_hook(client, _tap(pid, "c"))
    assert tg.of("editMessageReplyMarkup") == [{"chat_id": OWNER, "message_id": 555,
                                                "reply_markup": rs.retract_keyboard(pid)}]
    assert _answers(tg) == ["Kept"]
    assert _post(ledger, pid) == before and wakes == [] and platform_apis == []


@pytest.mark.parametrize("status", ["pending_review", "approved", "queued", "failed", "retracted", "skipped",
                                    "rejected"])
@pytest.mark.parametrize("verb", ["d", "k"])
def test_retract_taps_on_a_post_that_is_not_published_decide_nothing(client, ledger, tg, wakes, platform_apis,
                                                                      status, verb):
    pid = _seed_published(ledger, status=status)
    before = _snap(ledger, pid)
    assert _post_hook(client, _tap(pid, verb)).status_code == 200
    assert _post(ledger, pid) == before
    assert wakes == [] and platform_apis == []
    assert tg.of("editMessageReplyMarkup") == []
    expected = f"Nothing to do — the post is {status}" if verb == "d" else f"Not deleted — the post is {status}"
    assert _answers(tg) == [expected]


@pytest.mark.parametrize("verb", ["d", "k", "l", "n"])
def test_a_tap_on_an_unknown_post_is_not_found(client, ledger, tg, wakes, verb):
    assert _post_hook(client, _tap(_PID, verb)).status_code == 200
    assert _answers(tg) == ["Post not found"]
    assert wakes == [] and tg.of("editMessageReplyMarkup") == []


def test_a_ledger_failure_on_confirm_asks_for_another_tap(client, ledger, tg, wakes):
    pid = _seed_published(ledger)
    before = _snap(ledger, pid)
    ledger.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 520"))
    assert _post_hook(client, _tap(pid, "k")).status_code == 200
    assert _post(ledger, pid) == before
    assert _answers(tg) == ["Could not record that — tap again"]
    assert tg.of("editMessageText") == [] and wakes == []


def test_a_confirm_that_keeps_losing_its_fence_is_busy_not_requested(client, ledger, tg, wakes, monkeypatch):
    pid = _seed_published(ledger)
    before = _snap(ledger, pid)

    async def lost_fence(*a, **k):
        return None

    monkeypatch.setattr(ledger, "transition_post", lost_fence)
    _post_hook(client, _tap(pid, "k"))
    assert _post(ledger, pid) == before
    assert _answers(tg) == ["Could not record that — tap again"]
    assert tg.of("editMessageText") == [] and wakes == []      # the buttons stay for another tap


def _seed_escalated(svc, *, publish: Any = "__escalated__", status: str = "queued") -> str:
    meta: Dict[str, Any] = {
        "dry_run": False, "review_notified_at": "2026-09-28T10:00:00+00:00", "review_message_id": 500,
        "alert_kind": "unknown", "alert_text": "⚠️ X post — outcome UNKNOWN",
        "alert_notified_at": "2026-09-28T16:00:00+00:00", "alert_message_id": 777,
    }
    if publish == "__escalated__":
        meta["publish"] = {"state": "escalated", "escalated_at": "2026-09-28T16:00:00+00:00", "attempt": 1}
    elif publish is not None:
        meta["publish"] = publish
    pid = _seed_post(svc, platform="x", fmt="text", title=None, status=status, meta=meta)
    _post(svc, pid)["attempts"] = 1
    return pid


def test_its_live_publishes_an_escalated_post_and_wakes_the_publisher(client, ledger, tg, wakes, platform_apis):
    pid = _seed_escalated(ledger)
    _post_hook(client, _tap(pid, "l", message_id=777, text="⚠️ X post — outcome UNKNOWN"))
    row = _post(ledger, pid)
    assert row["status"] == "published" and row["published_at"]
    assert row["last_error"] == "owner confirmed it is live; the platform id is unknown"
    assert row.get("external_id") is None                      # never invented
    meta = row["metadata"]
    assert meta["owner_outcome"]["decision"] == "live" and meta["owner_outcome"]["by"] == f"telegram:{OWNER}"
    assert meta["publish"]["state"] == "published"
    assert meta["publish"]["escalated_at"] == "2026-09-28T16:00:00+00:00"   # merged, not replaced
    assert meta["alert_message_id"] == 777 and meta["review_message_id"] == 500
    assert wakes == [1] and platform_apis == []
    assert re.fullmatch(r"✅ Marked live \d\d:\d\d ET", _answers(tg)[0])
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == 777 and edit["reply_markup"] == {"inline_keyboard": []}
    assert re.search(r"\n\n✅ Marked live \d\d:\d\d ET$", edit["text"])


def test_not_posted_fails_an_escalated_post_and_wakes_the_publisher(client, ledger, tg, wakes, platform_apis):
    pid = _seed_escalated(ledger)
    _post_hook(client, _tap(pid, "n", message_id=777))
    row = _post(ledger, pid)
    assert row["status"] == "failed" and row["last_error"] == "owner confirmed it was not posted"
    assert row["metadata"]["publish"]["state"] == "owner_not_posted"
    assert row["metadata"]["owner_outcome"]["decision"] == "not_posted"
    assert wakes == [1] and platform_apis == []
    assert re.fullmatch(r"❌ Marked not posted \d\d:\d\d ET", _answers(tg)[0])


@pytest.mark.parametrize("publish", [{"state": "unknown"}, {"state": "sending"}, {"state": "published"},
                                     None, "escalated", ["escalated"]])
@pytest.mark.parametrize("verb", ["l", "n"])
def test_an_answer_on_a_post_that_is_not_escalated_decides_nothing(client, ledger, tg, wakes, publish, verb):
    pid = _seed_escalated(ledger, publish=publish)
    before = _snap(ledger, pid)
    _post_hook(client, _tap(pid, verb))
    assert _post(ledger, pid) == before
    assert _answers(tg) == ["Nothing to decide any more"] and wakes == []


@pytest.mark.parametrize("status", ["published", "failed", "approved", "retracted"])
def test_an_answer_after_the_outcome_is_known_says_already(client, ledger, tg, wakes, status):
    pid = _seed_escalated(ledger, status=status)
    before = _snap(ledger, pid)
    _post_hook(client, _tap(pid, "l"))
    assert _post(ledger, pid) == before
    assert _answers(tg) == [f"Already {status}"] and wakes == []


def test_a_double_its_live_writes_once(client, ledger, tg, wakes):
    pid = _seed_escalated(ledger)
    _post_hook(client, _tap(pid, "l"))
    after_first = _snap(ledger, pid)
    _post_hook(client, _tap(pid, "n"))           # a late "Not posted" cannot flip it
    assert _post(ledger, pid) == after_first
    assert _answers(tg)[1] == "Already published" and wakes == [1]


def test_approve_wakes_the_publisher_once_and_reject_does_not(client, ledger, tg, wakes):
    approved, rejected = _seed_post(ledger), _seed_post(ledger)
    _post_hook(client, _tap(approved, "a"))
    assert wakes == [1]
    _post_hook(client, _tap(approved, "a"))      # "Already approved" — no second wake
    _post_hook(client, _tap(rejected, "r"))
    assert wakes == [1]
    assert _post(ledger, approved)["status"] == "approved" and _post(ledger, rejected)["status"] == "rejected"


@pytest.mark.parametrize("kw", [{"from_id": OWNER + 1}, {"chat_id": OWNER + 1}, {"from_id": str(OWNER)},
                                {"drop": ("message",)}])
@pytest.mark.parametrize("verb", ["d", "k", "c", "l", "n"])
def test_every_new_verb_respects_the_owner_allow_list(client, ledger, tg, wakes, platform_apis, verb, kw):
    pid = _seed_escalated(ledger) if verb in ("l", "n") else _seed_published(ledger)
    before = _snap(ledger, pid)
    assert _post_hook(client, _tap(pid, verb, **kw)).status_code == 200
    assert _post(ledger, pid) == before
    assert tg.of("answerCallbackQuery") == [{"callback_query_id": "cbq-1", "text": "Not allowed"}]
    assert tg.of("editMessageReplyMarkup") == [] and tg.of("editMessageText") == [] and tg.of("sendMessage") == []
    assert wakes == [] and platform_apis == []


@pytest.mark.parametrize("data", ["D:{pid}", "k:{PID}", "retract:{pid}", "x:{pid}", "K:{pid}", "kk:{pid}"])
def test_malformed_new_verbs_through_the_webhook_decide_nothing(client, ledger, tg, wakes, data):
    pid = _seed_published(ledger)
    before = _snap(ledger, pid)
    _post_hook(client, _tap(pid, data=data.format(pid=pid, PID=pid.upper())))
    assert _post(ledger, pid) == before
    assert _answers(tg) == ["Unknown action"] and wakes == []


# ── review 2026-09-30: an Approve that cannot send says so; a Retract that cannot delete says so ──


@pytest.mark.asyncio
@pytest.mark.parametrize("switch, value, phrase", [
    ("MARKETING_ENABLED", False, "publishing is OFF on the web"),
    ("MARKETING_DRY_RUN", True, "the web is in DRY RUN"),
])
async def test_a_live_post_says_when_approving_will_not_send_it(ledger, tg, monkeypatch, switch, value, phrase):
    monkeypatch.setattr(settings, switch, value)
    _seed_run(ledger, RUN_A)
    _seed_post(ledger, RUN_A, "x", "text", title=None, caption="Live words")
    await rs.review_cycle()
    (msg,) = tg.of("sendMessage")
    assert msg["text"].startswith(f"X · text · run 2026-09-28 · ⚠️ {phrase}: approving will not send it")
    assert msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("a:")


def test_approving_while_the_web_cannot_send_warns_in_the_toast_and_the_message(client, ledger, tg, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_DRY_RUN", True)
    pid = _seed_post(ledger)
    assert _post_hook(client, _tap(pid)).status_code == 200
    assert _post(ledger, pid)["status"] == "approved"            # the decision is still recorded
    toast = tg.of("answerCallbackQuery")[0]["text"]
    assert "DRY RUN" in toast and "nothing will be sent" in toast
    assert "NOT sent" in tg.of("editMessageText")[0]["text"]


def test_retract_on_a_platform_that_cannot_delete_records_nothing(client, ledger, tg, monkeypatch, platform_apis):
    monkeypatch.setattr(rs.outlets, "retract_capable", lambda _platform: False)
    pid = _seed_published(ledger, "x")
    before = copy.deepcopy(_post(ledger, pid))
    for verb in ("d", "k"):
        assert _post_hook(client, _tap(pid, verb)).status_code == 200
    assert _post(ledger, pid) == before
    assert any("remove it by hand" in m["text"] for m in tg.of("editMessageText"))
    assert platform_apis == []


# ══ Reject reasons (2026-10-01): ❌ offers a reason keyboard; a tap records `metadata.review.reason` ══


_REASON_VERBS = [("t", "tone", "Tone"), ("f", "accuracy", "Accuracy"), ("p", "compliance", "Compliance"),
                 ("w", "weak", "Weak / boring"), ("o", "other", "Other")]
_TEXT = "TIKTOK · video · run 2026-09-28\n\nHello caption"


def test_the_reason_labels_are_keyed_by_exactly_the_ledgers_codes():
    assert tuple(rs.REJECT_REASONS) == mrs.REJECT_REASON_CODES
    assert [(code, label) for _v, code, label in _REASON_VERBS] == list(rs.REJECT_REASONS.items())
    for verb, code, _label in _REASON_VERBS:
        assert rs._VERBS[verb] == f"reason_{code}"
    # every verb is ONE lowercase ASCII letter (so the callback pattern stays a character class) and
    # names one decision
    assert all(len(v) == 1 and v.isascii() and v.islower() and v.isalpha() for v in rs._VERBS)
    assert len(set(rs._VERBS.values())) == len(rs._VERBS)
    assert rs._CALLBACK_RE.pattern.startswith("([") and "x" not in rs._VERBS and "e" not in rs._VERBS


def test_the_reason_keyboard_is_one_button_per_reason_within_64_bytes():
    kb = rs.reject_reason_keyboard(_PID)
    buttons = [b for row in kb["inline_keyboard"] for b in row]
    assert [b["text"] for b in buttons] == list(rs.REJECT_REASONS.values())
    assert [b["callback_data"] for b in buttons] == [f"{v}:{_PID}" for v, _c, _l in _REASON_VERBS]
    assert all(len(b["callback_data"].encode("utf-8")) <= 64 for b in buttons)
    with pytest.raises(ValueError):
        rs.reject_reason_keyboard(_PID.upper())


def _seed_rejected(svc, *, reason: Optional[str] = None, status: str = "rejected") -> str:
    review: Dict[str, Any] = {"decision": "rejected", "by": f"telegram:{OWNER}", "at": "2026-09-28T10:05:00+00:00"}
    if reason:
        review["reason"] = reason
    return _seed_post(svc, status=status, meta={"dry_run": False, "review_notified_at": "2026-09-28T10:00:00+00:00",
                                               "review_message_id": 555, "review": review})


def test_a_reject_swaps_the_buttons_for_the_reason_keyboard_on_the_same_message(client, ledger, tg, wakes):
    pid = _seed_post(ledger)
    assert _post_hook(client, _tap(pid, "r")).status_code == 200
    assert _post(ledger, pid)["status"] == "rejected" and wakes == []
    assert _answers(tg) == ["Rejected ❌"]
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == 555 and edit["reply_markup"] == rs.reject_reason_keyboard(pid)
    assert re.fullmatch(re.escape(_TEXT) + r"\n\n❌ Rejected \d\d:\d\d ET", edit["text"])
    assert "parse_mode" not in edit
    assert tg.of("sendMessage") == [] and tg.of("editMessageReplyMarkup") == []


@pytest.mark.parametrize("how", ["edit_refused", "edit_unavailable", "no_message_id"])
def test_a_reject_whose_message_cannot_be_edited_offers_the_reasons_on_a_new_message(client, ledger, tg, how):
    pid = _seed_post(ledger)
    if how == "edit_refused":
        tg.script["editMessageText"] = [(400, {"ok": False, "description": "Bad Request: message can't be edited"})]
    elif how == "edit_unavailable":
        tg.script["editMessageText"] = [(502, b"")]
    _post_hook(client, _tap(pid, "r", message_id=None if how == "no_message_id" else 555))
    assert _post(ledger, pid)["status"] == "rejected"           # the decision stands either way
    assert len(tg.of("editMessageText")) == (0 if how == "no_message_id" else 1)
    (msg,) = tg.of("sendMessage")
    assert msg["text"] == "Why was the TIKTOK post rejected? Tap a reason (optional)."
    assert msg["reply_markup"] == rs.reject_reason_keyboard(pid) and "parse_mode" not in msg


def test_a_reject_whose_fallback_also_fails_still_answers_and_keeps_the_decision(client, ledger, tg, caplog):
    pid = _seed_post(ledger)
    tg.script["editMessageText"] = [(400, {"ok": False, "description": "Bad Request: message to edit not found"})]
    tg.script["sendMessage"] = [(502, b"")]
    caplog.set_level(logging.WARNING)
    assert _post_hook(client, _tap(pid, "r")).status_code == 200
    assert _post(ledger, pid)["status"] == "rejected" and _answers(tg) == ["Rejected ❌"]
    assert any("could not offer the keyboard" in r.getMessage() for r in caplog.records)


def test_an_unexpected_edit_error_still_offers_the_reasons_and_never_raises(client, ledger, tg, monkeypatch, caplog):
    pid = _seed_post(ledger)

    async def broken_edit(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(rs.telegram, "edit_message_text", broken_edit)
    caplog.set_level(logging.ERROR)
    r = _post_hook(client, _tap(pid, "r"))
    assert r.status_code == 200 and _post(ledger, pid)["status"] == "rejected"
    assert tg.of("sendMessage")[0]["reply_markup"] == rs.reject_reason_keyboard(pid)
    assert any("keyboard edit raised" in rec.getMessage() for rec in caplog.records)
    assert not any("update handling raised" in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize("first, second", [("a", None), ("a", "r"), ("r", "a")])
def test_only_a_reject_offers_reasons(client, ledger, tg, first, second):
    """Approve, an approve tap on a rejected post, and a reject tap on an approved one: the buttons are
    removed, never swapped for reasons. (A reject — and a reject replay — is the one that offers them.)"""
    pid = _seed_post(ledger)
    _post_hook(client, _tap(pid, first))
    if second:
        _post_hook(client, _tap(pid, second))
    edits = tg.of("editMessageText")
    if first == "r":
        assert edits[0]["reply_markup"] == rs.reject_reason_keyboard(pid)
        edits = edits[1:]
    assert edits and all(e["reply_markup"] == {"inline_keyboard": []} for e in edits)
    assert tg.of("sendMessage") == []


def test_an_unknown_post_never_offers_reasons(client, ledger, tg):
    _post_hook(client, _tap(_PID, "r"))
    assert _answers(tg) == ["Post not found"]
    assert tg.of("editMessageText")[0]["reply_markup"] == {"inline_keyboard": []}


def test_a_replayed_reject_re_offers_the_reasons_until_one_is_recorded(client, ledger, tg):
    """Telegram replays an update it thinks we missed: the replay is `already_rejected`, and while no
    reason is recorded it must not take the reason keyboard away again."""
    pid = _seed_post(ledger)
    _post_hook(client, _tap(pid, "r"))
    snapshot = _snap(ledger, pid)
    _post_hook(client, _tap(pid, "r"))                      # the replay
    assert _post(ledger, pid) == snapshot                   # nothing written
    assert _answers(tg)[1].startswith("Already rejected")
    assert tg.of("editMessageText")[1]["reply_markup"] == rs.reject_reason_keyboard(pid)
    _post_hook(client, _tap(pid, "w"))                      # a reason is recorded
    _post_hook(client, _tap(pid, "r"))                      # another replay: nothing left to ask
    assert tg.of("editMessageText")[-1]["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.parametrize("verb, code, label", _REASON_VERBS)
def test_each_reason_tap_records_it_and_closes_the_keyboard(client, ledger, tg, wakes, platform_apis, verb, code,
                                                            label):
    pid = _seed_post(ledger)
    _post_hook(client, _tap(pid, "r"))
    before = _snap(ledger, pid)
    rejected_text = tg.of("editMessageText")[0]["text"]
    r = _post_hook(client, _tap(pid, verb, text=rejected_text))
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = _post(ledger, pid)
    assert row["status"] == "rejected"
    review = row["metadata"]["review"]
    assert (review["reason"], review["reason_by"], review["decision"]) == (code, f"telegram:{OWNER}", "rejected")
    for key, value in before["metadata"]["review"].items():    # the decision record is kept
        assert review[key] == value, key
    assert _answers(tg)[-1] == f"Reason saved: {label}"
    edit = tg.of("editMessageText")[-1]
    assert edit["text"] == f"{rejected_text}\n\nReason: {label}"
    assert edit["reply_markup"] == {"inline_keyboard": []} and edit["message_id"] == 555
    assert wakes == [] and platform_apis == []                    # a reason wakes and calls nothing


def test_a_double_reason_tap_writes_once(client, ledger, tg):
    pid = _seed_rejected(ledger)
    _post_hook(client, _tap(pid, "t"))
    after_first = _snap(ledger, pid)
    _post_hook(client, _tap(pid, "t"))           # a second tap, or Telegram replaying the update
    assert _post(ledger, pid) == after_first
    assert _answers(tg) == ["Reason saved: Tone", "Reason already saved: Tone"]
    first, second = tg.of("editMessageText")
    assert first["text"] == second["text"] == f"{_TEXT}\n\nReason: Tone"   # the same message, not appended twice


def test_a_later_different_reason_wins(client, ledger, tg):
    pid = _seed_rejected(ledger)
    _post_hook(client, _tap(pid, "t"))
    _post_hook(client, _tap(pid, "w"))
    assert _post(ledger, pid)["metadata"]["review"]["reason"] == "weak"
    assert _answers(tg) == ["Reason saved: Tone", "Reason saved: Weak / boring"]


@pytest.mark.parametrize("kw", [{"from_id": OWNER + 1}, {"chat_id": OWNER + 1}, {"from_id": str(OWNER)},
                                {"chat_id": -100123}, {"drop": ("message",)}, {"drop": ("from",)}])
@pytest.mark.parametrize("verb", ["t", "o"])
def test_a_reason_tap_from_anyone_but_the_owner_is_refused(client, ledger, tg, wakes, verb, kw):
    pid = _seed_rejected(ledger)
    before = _snap(ledger, pid)
    assert _post_hook(client, _tap(pid, verb, **kw)).status_code == 200
    assert _post(ledger, pid) == before
    assert tg.of("answerCallbackQuery") == [{"callback_query_id": "cbq-1", "text": "Not allowed"}]
    assert tg.of("editMessageText") == [] and tg.of("sendMessage") == [] and wakes == []


@pytest.mark.parametrize("status", ["pending_review", "approved", "queued", "published", "failed", "skipped",
                                    "retracted"])
def test_a_reason_tap_on_a_post_that_is_not_rejected_answers_and_writes_nothing(client, ledger, tg, status):
    pid = _seed_rejected(ledger, status=status)
    before = _snap(ledger, pid)
    assert _post_hook(client, _tap(pid, "f")).status_code == 200
    assert _post(ledger, pid) == before
    assert _answers(tg) == [f"No reason recorded — the post is {status}"]
    assert tg.of("editMessageText") == [] and tg.of("sendMessage") == []


def test_a_reason_tap_on_an_unknown_post_is_not_found(client, ledger, tg):
    assert _post_hook(client, _tap(_PID, "p")).status_code == 200
    assert _answers(tg) == ["Post not found"]
    (edit,) = tg.of("editMessageText")
    assert edit["text"] == f"{_TEXT}\n\nPost not found" and edit["reply_markup"] == {"inline_keyboard": []}


def test_a_reason_ledger_failure_keeps_the_keyboard_for_another_tap(client, ledger, tg, monkeypatch, caplog):
    pid = _seed_rejected(ledger)
    before = _snap(ledger, pid)
    ledger.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 520"))
    caplog.set_level(logging.ERROR)
    assert _post_hook(client, _tap(pid, "t")).status_code == 200
    assert _post(ledger, pid) == before
    assert _answers(tg) == ["Could not record that — tap again"]
    assert tg.of("editMessageText") == []
    assert any("record_reject_reason FAILED" in r.getMessage() for r in caplog.records)


def test_a_reason_tap_that_keeps_losing_its_fence_is_busy_and_keeps_the_keyboard(client, ledger, tg, monkeypatch):
    pid = _seed_rejected(ledger)
    before = _snap(ledger, pid)

    async def lost_fence(*a, **k):
        return None

    monkeypatch.setattr(ledger, "transition_post", lost_fence)
    _post_hook(client, _tap(pid, "t"))
    assert _post(ledger, pid) == before
    assert _answers(tg) == ["Could not record that — tap again"] and tg.of("editMessageText") == []


def test_telegram_failures_while_answering_a_reason_never_undo_it(client, ledger, tg):
    pid = _seed_rejected(ledger)
    tg.script["answerCallbackQuery"] = [(400, {"ok": False, "description": "Bad Request: query is too old"})]
    tg.script["editMessageText"] = [(502, b"")]
    assert _post_hook(client, _tap(pid, "o")).status_code == 200
    assert _post(ledger, pid)["metadata"]["review"]["reason"] == "other"


def test_a_reason_tap_without_a_message_still_records_it(client, ledger, tg):
    pid = _seed_rejected(ledger)
    _post_hook(client, _tap(pid, "w", message_id=None, text=None))
    assert _post(ledger, pid)["metadata"]["review"]["reason"] == "weak"
    assert _answers(tg) == ["Reason saved: Weak / boring"] and tg.of("editMessageText") == []


@pytest.mark.parametrize("data", ["T:{pid}", "tone:{pid}", "reason_tone:{pid}", "t:{PID}", "t:{pid}\n", "tf:{pid}",
                                  "t;{pid}", "t:"])
def test_malformed_reason_callbacks_decide_nothing(client, ledger, tg, data):
    pid = _seed_rejected(ledger)
    before = _snap(ledger, pid)
    _post_hook(client, _tap(pid, data=data.format(pid=pid, PID=pid.upper())))
    assert _post(ledger, pid) == before
    assert _answers(tg) == ["Unknown action"]


@pytest.mark.asyncio
async def test_a_verb_whose_reason_has_no_label_records_nothing(ledger, tg, monkeypatch, caplog):
    """The two tables drifting (a verb added without its label) is refused loudly, never recorded."""
    pid = _seed_rejected(ledger)
    before = _snap(ledger, pid)
    monkeypatch.setitem(rs._VERBS, "q", "reason_spam")
    monkeypatch.setattr(rs, "_CALLBACK_RE", re.compile(rf"([{''.join(rs._VERBS)}]):({rs._UUID})", re.ASCII))
    caplog.set_level(logging.ERROR)
    out = await rs.handle_update(_tap(pid, data=f"q:{pid}"))
    assert out == {"ok": True, "refused": "unknown_action"} and _post(ledger, pid) == before
    assert _answers(tg) == ["Unknown action"]
    assert any("has no label" in r.getMessage() for r in caplog.records)


_NOT_MODIFIED = (400, {"ok": False, "description": "Bad Request: message is not modified: specified new message "
                                                     "content and reply markup are exactly the same as a current "
                                                     "content and reply markup of the message"})


def test_a_reject_replay_whose_message_already_shows_the_reasons_sends_nothing_new(client, ledger, tg):
    """Telegram answers "message is not modified" when the edit changes nothing (a second replay of
    the same update): the reasons are already on screen, so no fallback message repeats them."""
    pid = _seed_post(ledger)
    _post_hook(client, _tap(pid, "r"))
    tg.script["editMessageText"] = [_NOT_MODIFIED]
    _post_hook(client, _tap(pid, "r"))
    assert len(tg.of("editMessageText")) == 2 and tg.of("sendMessage") == []
    assert _post(ledger, pid)["status"] == "rejected"


def test_a_retract_replay_whose_keyboard_is_already_in_place_sends_nothing_new(client, ledger, tg, wakes):
    pid = _seed_published(ledger)
    tg.script["editMessageReplyMarkup"] = [_NOT_MODIFIED]
    _post_hook(client, _tap(pid, "d"))
    assert len(tg.of("editMessageReplyMarkup")) == 1 and tg.of("sendMessage") == []
    assert _answers(tg) == ["Confirm the delete"] and wakes == []


# ── drop 1 review fixes (2026-10-09): compat:F3, bundles:RB-1, server:F1 (second guard) ──────────────
#
# Mutation-checked by hand on 2026-10-09 against a mutated COPY of review_service.py preloaded into
# sys.modules: removing the MARKETING_X_IMAGES branch of `web_send_blocker` turned the two F3 tests red;
# removing the `_offer_drop_reasons` calls turned the two RB-1 tests red; keeping every video member
# whatever it carries (the pre-fix behaviour) turned the two left-out tests red, while the normal-day
# test stayed green, as it must. Restored.


def _x_image_post(**meta) -> Dict[str, Any]:
    return {"id": str(uuid.uuid4()), "platform": "x", "format": "image", "caption": "Live words", "title": None,
            "asset_ids": [], "metadata": {"dry_run": False, **meta}}


_X_IMAGES_OFF = "MARKETING_X_IMAGES is off (X image posts are refused)"
_X_IMAGES_OFF_BEFORE = "approving will mark X failed — reject it, or turn MARKETING_X_IMAGES on first"
#: A bundle of two or more members carries "✂ drop", so only there is the remedy "drop X" (re-review r3).
_X_IMAGES_OFF_BEFORE_DROP = "approving will mark X failed — ✂ drop X, or turn MARKETING_X_IMAGES on first"


@pytest.mark.parametrize("platform, fmt, x_images, rehearsal, warned", [
    ("x", "image", False, False, True),      # the publisher would refuse it → every review text warns
    ("x", "image", True, False, False),      # the switch is on: it goes out
    ("x", "text", False, False, False),      # a text X post never needs the switch
    ("bluesky", "image", False, False, False),   # the switch is X's only
    ("x", "image", False, True, False),      # a rehearsal row says DRY RUN already
])
def test_an_x_image_post_with_the_x_image_switch_off_warns_in_the_per_post_header(
        configured, monkeypatch, platform, fmt, x_images, rehearsal, warned):
    monkeypatch.setattr(settings, "MARKETING_X_IMAGES", x_images)
    post = {**_x_image_post(), "platform": platform, "format": fmt}
    if rehearsal:
        post["metadata"] = {"dry_run": True}
    assert rs.web_send_blocker(post) == (_X_IMAGES_OFF if warned else None)
    header = rs.compose_post_text(post, "2026-10-09", []).split("\n\n", 1)[0]
    expected = f"{platform.upper()} · {fmt} · run 2026-10-09" + (" · DRY RUN" if rehearsal else "")
    if warned:   # r3: the failure and its remedy are said BEFORE the decision
        expected += f" · ⚠️ {_X_IMAGES_OFF}: {_X_IMAGES_OFF_BEFORE}"
    assert header == expected


def test_an_x_image_post_with_the_x_image_switch_off_warns_in_the_bundle_decision_and_result(
        configured, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_X_IMAGES", False)
    x_post = _x_image_post()
    bluesky = {**_x_image_post(), "platform": "bluesky"}
    text = rs.compose_bundle_decision_text("post", [x_post, bluesky], [], "2026-10-09", False, [])
    assert f"⚠️ {_X_IMAGES_OFF}: {_X_IMAGES_OFF_BEFORE_DROP}" in text.split("\n")
    assert "✂ drop = " in text      # the control the remedy names is really on this message
    lines, blocked = rs.bundle_result_lines([{"outcome": "approved", "row": x_post},
                                             {"outcome": "approved", "row": bluesky}])
    assert blocked is True
    assert lines == [f"• X (image): approved — ⚠️ {_X_IMAGES_OFF}: {_X_IMAGES_OFF_FAILS}",
                     "• BLUESKY (image): approved"]
    monkeypatch.setattr(settings, "MARKETING_X_IMAGES", True)
    assert "⚠️" not in rs.compose_bundle_decision_text("post", [x_post, bluesky], [], "2026-10-09", False, [])


# ── drop 1 re-review (2026-10-09): the X-image blocker names its OWN consequence ─────────────────────
#
# Every other blocker leaves an approved post `approved` until it expires, so "NOT sent (it expires after
# its day)" is true for them and stays byte-for-byte. An X image post with MARKETING_X_IMAGES off is
# different: the Approve wakes the publisher, `outlet_x.prepare` refuses it and `_refuse` marks it
# `failed` at once — so its line must say so, never promise an expiry. Mutation-checked by hand on
# 2026-10-09: giving the X-images blocker the shared consequence again turned the X-images cases red and
# left the other blockers' cases green. Restored.

# r3 (2026-10-09): after the approval only the outcome — the remedy moved before the decision.
_X_IMAGES_OFF_FAILS = "NOT sent: it will be marked failed (MARKETING_X_IMAGES is off)"
_STAYS_APPROVED = "NOT sent (it expires after its day)"


def _blocker_case(monkeypatch, case: str) -> str:
    """Narrow `configured` (everything can send) to exactly one blocker; returns its reason."""
    monkeypatch.setattr(settings, "MARKETING_X_IMAGES", case != "x_images_off")
    if case == "publishing_off":
        monkeypatch.setattr(settings, "MARKETING_ENABLED", False)
        return "publishing is OFF on the web"
    if case == "dry_run":
        monkeypatch.setattr(settings, "MARKETING_DRY_RUN", True)
        return "the web is in DRY RUN"
    if case == "not_enabled":
        monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: ["bluesky"])
        return "x is not enabled"
    assert case == "x_images_off"
    return _X_IMAGES_OFF


_BLOCKER_CASES = [("publishing_off", _STAYS_APPROVED), ("dry_run", _STAYS_APPROVED),
                  ("not_enabled", _STAYS_APPROVED), ("x_images_off", _X_IMAGES_OFF_FAILS)]


@pytest.mark.parametrize("case, consequence", _BLOCKER_CASES, ids=[c for c, _ in _BLOCKER_CASES])
def test_each_send_blocker_carries_its_own_consequence_in_the_bundle_result(configured, monkeypatch, case,
                                                                              consequence):
    reason = _blocker_case(monkeypatch, case)
    x_post = _x_image_post()
    assert rs.web_send_block(x_post) == rs.SendBlock(reason, consequence)
    assert rs.web_send_blocker(x_post) == reason                 # the notify-time headers read the reason alone
    lines, blocked = rs.bundle_result_lines([{"outcome": "approved", "row": x_post}])
    assert blocked is True
    assert lines == [f"• X (image): approved — ⚠️ {reason}: {consequence}"]
    if case == "x_images_off":
        assert "expires" not in lines[0]


@pytest.mark.parametrize("case, consequence", _BLOCKER_CASES, ids=[c for c, _ in _BLOCKER_CASES])
def test_each_send_blocker_carries_its_own_consequence_in_the_per_post_approve_line(
        client, ledger, tg, wakes, monkeypatch, case, consequence):
    reason = _blocker_case(monkeypatch, case)
    pid = _seed_post(ledger, RUN_A, "x", "image", title=None, caption="Live words")
    assert _post_hook(client, _tap(pid, text="X · image · run 2026-09-28\n\nLive words")).status_code == 200
    assert _post(ledger, pid)["status"] == "approved" and wakes == [1]   # the decision is still recorded
    assert tg.of("answerCallbackQuery")[0]["text"] == f"Approved — but {reason}: nothing will be sent"
    (edit,) = tg.of("editMessageText")
    assert re.fullmatch(r"X · image · run 2026-09-28\n\nLive words\n\n✅ Approved \d\d:\d\d ET — ⚠️ "
                        + re.escape(f"{reason}: {consequence}"), edit["text"]), edit["text"]


class _IdTelegram(FakeTelegram):
    """FakeTelegram that also remembers the message id of each answer (None for a non-message)."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: List[Optional[int]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        resp = super().handler(request)
        body = json.loads(resp.content or b"{}")
        result = body.get("result") if isinstance(body, dict) else None
        self.ids.append(result.get("message_id") if isinstance(result, dict) else None)
        return resp


@pytest.fixture
def tgi(monkeypatch):
    fake = _IdTelegram()
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def _nested_col(row: Dict[str, Any], col: str) -> Any:
    """The fake's `_col` with PostgREST's nested JSON path (`a->b->>c`), as the bundle fence filters on."""
    if "->>" not in col:
        return row.get(col)
    path, key = col.rsplit("->>", 1)
    parts = path.split("->")
    doc: Any = row.get(parts[0])
    for part in parts[1:]:
        doc = doc.get(part) if isinstance(doc, dict) else None
    value = doc.get(key) if isinstance(doc, dict) else None
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


@pytest.fixture
def bundle_ledger(monkeypatch, ledger):
    import sys
    import test_marketing_run_service as fake_db
    monkeypatch.setattr(fake_db, "_col", _nested_col)
    monkeypatch.setattr(settings, "MARKETING_REVIEW_BUNDLES", True)
    # created_at's minutes wrap every 3,600 posts of the session-wide counter; member order follows it.
    monkeypatch.setattr(sys.modules[__name__], "_SEQ", iter(range(10, 3600)))
    return ledger


def _sent_decisions(tg: _IdTelegram) -> List[Dict[str, Any]]:
    out = []
    for (method, payload), mid in zip(tg.calls, tg.ids):
        datas = [b["callback_data"] for row in (payload.get("reply_markup") or {}).get("inline_keyboard", [])
                 for b in row]
        if method == "sendMessage" and any(d.startswith("g:") for d in datas):
            out.append({**payload, "message_id": mid})
    return out


def _tap_message(message: Dict[str, Any], verb: str, target: str) -> Dict[str, Any]:
    """A tap on `message` as Telegram returns it (id, text and keyboard included)."""
    msg = {"message_id": message["message_id"], "date": 1759000000, "chat": {"id": OWNER, "type": "private"},
           "text": message.get("text")}
    if message.get("reply_markup") is not None:
        msg["reply_markup"] = message["reply_markup"]
    return {"update_id": 9100, "callback_query": {"id": "cbq-d", "from": {"id": OWNER, "is_bot": False},
                                                  "message": msg, "chat_instance": "ci", "data": f"{verb}:{target}"}}


@pytest.mark.asyncio
async def test_a_bundle_drop_offers_the_reasons_and_a_reason_lands_on_the_dropped_post_only(
        bundle_ledger, tgi, wakes):
    svc = bundle_ledger
    _seed_run(svc, video_id=None)
    ids = {p: _seed_post(svc, RUN_A, p, "text", caption="Same words.", title=None) for p in ("x", "bluesky", "threads")}
    await rs.review_cycle()
    (decision,) = _sent_decisions(tgi)
    bid = decision["reply_markup"]["inline_keyboard"][0][0]["callback_data"][2:]
    tgi.calls.clear()
    tgi.ids.clear()

    # ✂ drop BLUESKY → the decision message keeps the rest's buttons, and ONE small message under it offers
    # the per-post reasons for the dropped post.
    res = await rs.handle_update(_tap_message(decision, "s", ids["bluesky"]))
    assert res["outcome"] == "rejected"
    (edit,) = tgi.of("editMessageText")
    assert edit["reply_markup"] == rs.bundle_keyboard(bid, [_post(svc, ids["x"]), _post(svc, ids["threads"])])
    ((ask, ask_id),) = [(p, mid) for (m, p), mid in zip(tgi.calls, tgi.ids) if m == "sendMessage"]
    assert ask["text"] == "Why was the BLUESKY post dropped? Tap a reason (optional)."
    assert ask["reply_markup"] == rs.reject_reason_keyboard(ids["bluesky"])
    assert ask["reply_parameters"]["message_id"] == decision["message_id"]
    assert "parse_mode" not in ask

    # ✅ Approve all decides the other two…
    after_drop = {**decision, "text": edit["text"], "reply_markup": edit["reply_markup"]}
    res = await rs.handle_update(_tap_message(after_drop, "g", bid))
    assert res["decided"] == 2 and wakes == [1]

    # …and a reason tapped on the small message is recorded on the dropped post only.
    res = await rs.handle_update(_tap_message({"message_id": ask_id, "text": ask["text"],
                                               "reply_markup": ask["reply_markup"]}, "t", ids["bluesky"]))
    assert res["outcome"] == "recorded"
    dropped = _post(svc, ids["bluesky"])
    assert dropped["status"] == "rejected" and dropped["metadata"]["review"]["reason"] == "tone"
    for p in ("x", "threads"):
        assert _post(svc, ids[p])["status"] == "approved"
        assert "reason" not in (_post(svc, ids[p])["metadata"].get("review") or {})
    last = tgi.of("editMessageText")[-1]
    assert last["message_id"] == ask_id and last["text"].endswith("Reason: Tone")
    assert last["reply_markup"] == {"inline_keyboard": []}

    # A replayed drop once the reason is recorded asks nothing more.
    tgi.calls.clear()
    await rs.handle_update(_tap_message(decision, "s", ids["bluesky"]))
    assert tgi.of("sendMessage") == []


@pytest.mark.asyncio
async def test_a_failed_reason_offer_after_a_drop_is_a_warning_and_the_drop_stands(bundle_ledger, tgi, caplog):
    svc = bundle_ledger
    _seed_run(svc, video_id=None)
    a, b = (_seed_post(svc, RUN_A, p, "text", caption="Same words.", title=None) for p in ("x", "bluesky"))
    await rs.review_cycle()
    (decision,) = _sent_decisions(tgi)
    tgi.script["sendMessage"] = [(500, {"ok": False, "description": "Internal Server Error"})]
    caplog.set_level(logging.WARNING)
    res = await rs.handle_update(_tap_message(decision, "s", a))
    assert res == {"ok": True, "outcome": "rejected", "post_id": a}
    assert _post(svc, a)["status"] == "rejected" and _post(svc, b)["status"] == "pending_review"
    assert any(r.levelno == logging.WARNING and "could not offer the reject reasons after a drop" in r.getMessage()
               and a in r.getMessage() for r in caplog.records)


VIDEO_B = "55555555-5555-4555-8555-555555555555"


def _seed_second_video(svc, video_id: str = VIDEO_B) -> None:
    _rows(svc, mrs.ASSETS).append({
        "id": video_id, "run_id": RUN_A, "kind": "video", "status": "ready",
        "storage_path": f"2026-09-28/video/{'c' * 64}.mp4", "content_type": "video/mp4", "bytes": 5_000_000,
        "metadata": {}})


@pytest.mark.asyncio
async def test_a_video_member_carrying_another_video_is_left_out_of_the_bundle(bundle_ledger, tgi, wakes, caplog):
    svc = bundle_ledger
    _seed_run(svc)                       # VIDEO, the run's verified pointer
    _seed_second_video(svc)              # a second ready video of the same run
    ids = {"tiktok": _seed_post(svc, RUN_A, "tiktok", "video", caption="tt", title=None, asset_ids=[VIDEO]),
           "youtube": _seed_post(svc, RUN_A, "youtube", "video", caption="yt", title="YT", asset_ids=[VIDEO_B]),
           "instagram": _seed_post(svc, RUN_A, "instagram", "video", caption="ig", title=None, asset_ids=[VIDEO])}
    caplog.set_level(logging.ERROR)
    counters = await rs.review_cycle()
    assert counters["failed"] == 1 and counters["bundles"] == 1 and counters["stamped"] == 2
    (video,) = tgi.of("sendVideo")
    assert video["video"].endswith(f"/2026-09-28/video/{'a' * 64}.mp4")
    assert video["caption"].startswith("VIDEO · TIKTOK, INSTAGRAM · run 2026-09-28")
    texts = [p["text"] for p in tgi.of("sendMessage")]
    assert not any("YOUTUBE (video)" in t and t.startswith("CAPTION") for t in texts)   # its caption is not shown
    (decision,) = _sent_decisions(tgi)
    assert decision["text"].split("\n")[1] == "Approve all sends: TIKTOK (video), INSTAGRAM (video)"
    assert ("⚠️ Not in this decision: YOUTUBE — its post does not carry the video shown above (logged). Approve all "
            "does not cover it.") in decision["text"].split("\n")
    bid = decision["reply_markup"]["inline_keyboard"][0][0]["callback_data"][2:]
    assert decision["reply_markup"] == rs.bundle_keyboard(bid, [_post(svc, ids["tiktok"]), _post(svc, ids["instagram"])])
    yt = _post(svc, ids["youtube"])
    assert yt["status"] == "pending_review"
    assert "review_bundle" not in yt["metadata"] and "review_notified_at" not in yt["metadata"]
    assert _post(svc, ids["tiktok"])["metadata"]["review_bundle"]["members"] == [ids["tiktok"], ids["instagram"]]
    (err,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert ids["youtube"] in err.getMessage() and RUN_A in err.getMessage() and "LEFT OUT" in err.getMessage()

    # ✅ Approve all never reaches the video the owner was not shown.
    res = await rs.handle_update(_tap_message(decision, "g", bid))
    assert res["decided"] == 2 and wakes == [1]
    assert _post(svc, ids["youtube"])["status"] == "pending_review"
    assert {_post(svc, ids[p])["status"] for p in ("tiktok", "instagram")} == {"approved"}

    # The next sweep offers it on its own, showing ITS video — the owner decides what they saw.
    tgi.calls.clear()
    tgi.ids.clear()
    await rs.review_cycle()
    (video,) = tgi.of("sendVideo")
    assert video["video"].endswith(f"/2026-09-28/video/{'c' * 64}.mp4")
    (decision,) = _sent_decisions(tgi)
    assert decision["text"].split("\n")[1] == "Approve all sends: YOUTUBE (video)" and "⚠️" not in decision["text"]


@pytest.mark.asyncio
async def test_a_lone_video_member_that_cannot_match_the_shown_video_sends_nothing(bundle_ledger, tgi, caplog):
    """Two videos on one post (or one that is not a ready video of the run) never equals the one video shown:
    nothing is offered, nothing is stamped, and it is logged at ERROR every sweep until it expires."""
    svc = bundle_ledger
    _seed_run(svc)
    _seed_second_video(svc)
    pid = _seed_post(svc, RUN_A, "tiktok", "video", caption="tt", title=None, asset_ids=[VIDEO, VIDEO_B])
    caplog.set_level(logging.ERROR)
    counters = await rs.review_cycle()
    assert tgi.calls == [] and counters["failed"] == 1 and counters["bundles"] == 0
    assert _post(svc, pid)["status"] == "pending_review" and "review_bundle" not in _post(svc, pid)["metadata"]
    assert any(pid in r.getMessage() and "LEFT OUT" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_video_members_that_all_carry_the_shown_video_are_all_offered(bundle_ledger, tgi, caplog):
    """The guard leaves the normal day alone: every video post carries the run's one video."""
    svc = bundle_ledger
    _seed_run(svc)
    _seed_second_video(svc)              # present, but no post names it
    ids = [_seed_post(svc, RUN_A, p, "video", caption=p, title=None, asset_ids=[VIDEO])
           for p in ("tiktok", "youtube", "instagram")]
    caplog.set_level(logging.ERROR)
    counters = await rs.review_cycle()
    assert counters["failed"] == 0 and counters["stamped"] == 3
    (decision,) = _sent_decisions(tgi)
    assert "⚠️" not in decision["text"]
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert all(_post(svc, pid)["metadata"]["review_notified_at"] for pid in ids)


# ── drop 1 re-review r3 (2026-10-09): the X-image failure is said BEFORE the decision ─────────────────
#
# Its result line used to carry the only "will be marked failed" warning AND the remedy ("turn
# MARKETING_X_IMAGES on before approving, or drop it") — after the approval, when the bundle keyboard is
# gone and a ✂ drop can only answer `already_approved`. Now the per-post header and the bundle decision
# message say what approving would do and what to do first; the result line states only the outcome. Every
# other blocker's pre-decision line stays byte-identical. Mutation-checked by hand on 2026-10-09: restoring
# "approving will not send …" for the X-images blocker turned the x_images_off cases red (the other three
# stayed green); putting the remedy back into the consequence turned the result-line assertion red. Restored.

_BEFORE_CASES = [("publishing_off", "approving will not send it", "approving will not send X"),
                 ("dry_run", "approving will not send it", "approving will not send X"),
                 ("not_enabled", "approving will not send it", "approving will not send X"),
                 ("x_images_off", _X_IMAGES_OFF_BEFORE, _X_IMAGES_OFF_BEFORE)]


@pytest.mark.parametrize("case, header_tail, decision_tail", _BEFORE_CASES, ids=[c[0] for c in _BEFORE_CASES])
def test_each_blocker_says_before_the_decision_what_approving_would_do(configured, monkeypatch, case, header_tail,
                                                                        decision_tail):
    reason = _blocker_case(monkeypatch, case)
    x_post = _x_image_post()
    header = rs.compose_post_text(x_post, "2026-10-09", []).split("\n\n", 1)[0]
    assert header == f"X · image · run 2026-10-09 · ⚠️ {reason}: {header_tail}"
    text = rs.compose_bundle_decision_text("post", [x_post], [], "2026-10-09", False, [])
    assert text.split("\n")[2] == f"⚠️ {reason}: {decision_tail}"
    lines, _blocked = rs.bundle_result_lines([{"outcome": "approved", "row": x_post}])
    if case == "x_images_off":
        assert "mark X failed" in header and "mark X failed" in text      # the consequence, before the tap
        # …and after it only the outcome: no advice that can no longer be followed.
        assert lines == [f"• X (image): approved — ⚠️ {reason}: NOT sent: it will be marked failed "
                         "(MARKETING_X_IMAGES is off)"]
        assert "drop" not in lines[0] and "before approving" not in lines[0] and " first" not in lines[0]



def test_the_x_image_remedy_names_only_a_control_the_message_carries(configured, monkeypatch):
    """Re-review r3: "drop it" was offered on the per-post message and on a one-member bundle, neither of
    which has a ✂ drop button. Mutation-checked by hand: making `can_drop` always True turns this red."""
    monkeypatch.setattr(settings, "MARKETING_X_IMAGES", False)
    x_post = _x_image_post()
    header = rs.compose_post_text(x_post, "2026-10-09", []).split("\n\n", 1)[0]
    assert header.endswith(_X_IMAGES_OFF_BEFORE) and "drop" not in header
    one = rs.compose_bundle_decision_text("post", [x_post], [], "2026-10-09", False, [])
    assert f"⚠️ {_X_IMAGES_OFF}: {_X_IMAGES_OFF_BEFORE}" in one.split("\n") and "✂" not in one
    two = rs.compose_bundle_decision_text("post", [x_post, {**_x_image_post(), "platform": "bluesky"}], [],
                                          "2026-10-09", False, [])
    assert f"⚠️ {_X_IMAGES_OFF}: {_X_IMAGES_OFF_BEFORE_DROP}" in two.split("\n")
