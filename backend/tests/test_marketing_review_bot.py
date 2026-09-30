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
        if method in ("sendMessage", "sendVideo", "editMessageText"):
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


@pytest.mark.asyncio
async def test_the_tick_sweeps_even_with_publishing_off(monkeypatch):
    calls = []

    async def sweep():
        calls.append("review")
        return {"pending": 0, "sent": 0, "stamped": 0, "videos": 0, "failed": 0, "rate_limited": 0}

    async def publish():
        calls.append("publish")
        return {"approved_waiting": 0, "published": 0, "failed": 0, "skipped": 0}

    monkeypatch.setattr(pub.review_service, "is_configured", lambda: True)
    monkeypatch.setattr(pub.review_service, "review_cycle", sweep)
    monkeypatch.setattr(pub, "publish_cycle", publish)
    monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", False)
    await pub.publisher_tick()
    assert calls == ["review"]
    monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", True)
    await pub.publisher_tick()
    assert calls == ["review", "review", "publish"]
    monkeypatch.setattr(pub.review_service, "is_configured", lambda: False)
    await pub.publisher_tick()
    assert calls[-1] == "publish" and calls.count("review") == 2


@pytest.mark.asyncio
async def test_a_failing_sweep_never_stops_publishing(monkeypatch, caplog):
    calls = []

    async def sweep():
        raise RuntimeError("sweep exploded")

    async def publish():
        calls.append("publish")
        return {"approved_waiting": 0, "published": 0, "failed": 0, "skipped": 0}

    monkeypatch.setattr(pub.review_service, "is_configured", lambda: True)
    monkeypatch.setattr(pub.review_service, "review_cycle", sweep)
    monkeypatch.setattr(pub, "publish_cycle", publish)
    monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", True)
    caplog.set_level(logging.ERROR)
    await pub.publisher_tick()
    assert calls == ["publish"]
    assert any("marketing review sweep failed" in r.getMessage() and r.exc_info for r in caplog.records)


@pytest.mark.asyncio
async def test_the_loop_keeps_running_across_ticks(monkeypatch):
    ticks = []

    async def tick():
        ticks.append(1)
        if len(ticks) == 3:
            raise asyncio.CancelledError

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(pub, "publisher_tick", tick)
    monkeypatch.setattr(pub.asyncio, "sleep", no_sleep)
    with pytest.raises(asyncio.CancelledError):
        await pub.run_marketing_publisher_loop()
    assert len(ticks) == 3


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
