"""Report chat's web source pills — the placement helpers in `app/api/v1/endpoints/chat.py`
(2026-10-02).

The pills are built by `chat_web_search_service` (`WebSearchTurn.source_pills()`); the endpoint
places them — after the base pills, safe URLs only, one per host AND per publisher name, at most
`MAX_WEB_PILLS` — splits the LIVE list from the STORED one while `CHAT_WEB_SOURCES_PERSIST` is
False, drops an unsafe stored pill on read, and decides the `tool_step.skipped` flag.

Pure and hermetic: no network, no DB. The end-to-end pill test builds real pills through the
service's own digest (`_digest`) from a canned Brave answer.
"""

from __future__ import annotations

import copy

import pytest

from app.config import Settings
from app.schemas.chat import ChatSourcePill
import app.api.v1.endpoints.chat as ws
from app.services import chat_web_search_service as cws

_BASE = {"label": "Cay research report", "detail": "AAPL"}
_SCREEN = {"kind": "screen", "label": "Stock detail", "detail": "AAPL"}   # a non-web `kind`


def _pill(host="www.reuters.com", detail="Reuters", **kw):
    p = {"kind": "web", "label": "Web", "detail": detail, "title": "A title",
         "url": f"https://{host}/a/b", "published_at": "2026-09-30"}
    p.update(kw)
    return p


# ── wiring ───────────────────────────────────────────────────────────────────


def test_one_url_policy_for_building_and_reading():
    """Imported, never copied: the read-side check IS the builder's check."""
    assert ws._safe_https_url is cws._safe_https_url
    assert ws.MAX_WEB_PILLS == cws.MAX_WEB_PILLS == 5


def test_persistence_ships_on():
    """Owner decision 2026-10-09: no written storage confirmation from Brave is needed, and the
    DECLARED default — not this machine's .env — matches production, where the pills have been
    stored since 2026-10-03. Privacy §3 says the source list is saved; `tests/test_legal_pages.py`
    ties that sentence to this default, so the two can only flip together. The off path stays
    tested with the switch set explicitly (`_turn_sources(..., persist=False)` below, and the
    stream-endpoint tests)."""
    assert "CHAT_WEB_SOURCES_PERSIST" in Settings.model_fields
    assert Settings.model_fields["CHAT_WEB_SOURCES_PERSIST"].default is True


# ── is_web_pill / web_pill_host ─────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    (_pill(), True), (dict(_pill(), kind=" WEB "), True), (_BASE, False), (_SCREEN, False),
    (None, False), ("web", False), ([_pill()], False), ({"kind": 1}, False), ({}, False),
])
def test_is_web_pill(value, expected):
    assert ws._is_web_pill(value) is expected


def test_a_safe_pill_yields_its_own_host():
    assert ws._web_pill_host(_pill()) == "www.reuters.com"
    assert ws._web_pill_host(_pill(title=None, published_at=None)) == "www.reuters.com"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,<b>x</b>", "file:///etc/passwd", "mailto:a@b.com",
    "http://www.reuters.com/a",                 # https only (the builder's policy)
    "/relative/path", "https://", "https:///nohost", "ftp://reuters.com/x",
    "https://user@reuters.com/x", "https://user:pw@reuters.com/x",
    "https://reuters.com@evil.example/x",       # userinfo that reads as a publisher
    "https://reuters.com:8443/x",               # explicit port
    "https://127.0.0.1/x", "https://[::1]/x", "https://localhost/x", "https://printer.local/x",
    "https://0177.0.0.1/x",                     # octal-looking IP
    "https://evil.com\\.reuters.com/x",          # a backslash host
    "https://reu ters.com/x", "https://reuters.com/\nx", " ",
    "https://reuters.com/" + "a" * 2100,
    None, 42, ["https://reuters.com"],
])
def test_an_unsafe_url_is_never_a_pill(url):
    p = _pill()
    p["url"] = url
    assert ws._web_pill_host(p) is None, url


@pytest.mark.parametrize("field,value", [
    ("label", None), ("label", ""), ("label", "  "), ("label", 3),
    ("detail", None), ("detail", ""), ("detail", ["Reuters"]),
    ("title", 5), ("title", {"t": 1}), ("published_at", 20260930), ("published_at", ["x"]),
])
def test_a_malformed_field_makes_it_no_pill(field, value):
    p = _pill()
    p[field] = value
    assert ws._web_pill_host(p) is None


# ── merge / strip / turn_sources ─────────────────────────────────────────────


def test_base_pills_come_first_untouched_then_web_pills():
    base = [_BASE, _SCREEN]
    out = ws._merge_web_pills(base, [_pill(), _pill("apnews.com", "AP News")])
    assert out[:2] == [_BASE, _SCREEN] and out[0] is _BASE
    assert [p["detail"] for p in out[2:]] == ["Reuters", "AP News"]


def test_dedup_is_by_host_with_www_folded_and_by_publisher_name():
    out = ws._merge_web_pills([_BASE], [
        _pill("www.reuters.com"),
        _pill("reuters.com"),                    # the same host, `www.` folded
        _pill("uk.reuters.com"),                 # another host, the same publisher name
        _pill("uk.reuters.com", "reuters"),      # the name compared case-insensitively
        _pill("apnews.com", "AP News"),
    ])
    assert [p["detail"] for p in out[1:]] == ["Reuters", "AP News"]


def test_the_cap_is_five_web_pills_counting_only_kept_ones():
    pills = [_pill(url="javascript:x")] + [_pill(f"s{i}.example.com", f"S{i}") for i in range(9)]
    out = ws._merge_web_pills([_BASE], pills)
    assert len(out) == 1 + ws.MAX_WEB_PILLS
    assert [p["detail"] for p in out[1:]] == ["S0", "S1", "S2", "S3", "S4"]


def test_merge_is_idempotent_and_never_mutates_its_inputs():
    base = [_BASE, _pill("apnews.com", "AP News")]
    pills = [_pill(), _pill("apnews.com", "AP News")]
    b0, p0 = copy.deepcopy(base), copy.deepcopy(pills)
    once = ws._merge_web_pills(base, pills)
    assert ws._merge_web_pills(once, pills) == once
    assert ws._merge_web_pills(once, []) == once
    assert base == b0 and pills == p0
    once[1]["detail"] = "changed"
    assert base[1]["detail"] == "AP News", "the merged list holds copies of the web pills"


def test_web_pills_already_in_base_keep_their_place_before_new_ones():
    out = ws._merge_web_pills([_pill("apnews.com", "AP News"), _BASE], [_pill()])
    assert out == [_BASE, _pill("apnews.com", "AP News"), _pill()]


@pytest.mark.parametrize("base,pills", [(None, None), ("x", 5), ({"a": 1}, [None, 3, "s"]), ([], [])])
def test_junk_inputs_merge_to_an_empty_or_base_list(base, pills):
    assert ws._merge_web_pills(base, pills) == []


def test_strip_web_pills_removes_only_web_pills():
    assert ws._strip_web_pills([_BASE, _pill(), _SCREEN, None, "x"]) == [_BASE, _SCREEN, None, "x"]
    assert ws._strip_web_pills(None) == [] and ws._strip_web_pills("x") == []


def test_turn_sources_splits_live_from_stored():
    live, stored = ws._turn_sources([_BASE], [_pill()], persist=False)
    assert live == [_BASE, _pill()] and stored == [_BASE]
    live, stored = ws._turn_sources([_BASE], [_pill()], persist=True)
    assert live == stored == [_BASE, _pill()]
    assert ws._turn_sources(None, [], persist=False) == (None, None)
    assert ws._turn_sources([], [_pill()], persist=False) == ([_pill()], None)


# ── reading back ─────────────────────────────────────────────────────────────


def test_an_unsafe_stored_web_pill_is_dropped_and_everything_else_passes():
    stored = [_BASE, _pill(), _pill(url="javascript:alert(1)"), {"weird": True}, 7, None,
              dict(_pill(), kind="web", url="http://plain.example.com/x")]
    kept, dropped = ws._sanitize_stored_sources(stored)
    assert kept == [_BASE, _pill(), {"weird": True}, 7, None] and dropped == 2


@pytest.mark.parametrize("value", [None, "x", {"sources": []}, 3])
def test_a_non_list_is_returned_as_is(value):
    assert ws._sanitize_stored_sources(value) == (value, 0)


def test_overlay_replaces_only_the_top_level_sources():
    msg = {"id": "m", "sources": [_BASE], "rich_content": {"sources": [_BASE]},
           "thinking": {"source_count": 1}}
    out = ws._overlay_live_sources(msg, [_BASE, _pill()])
    assert out["sources"] == [_BASE, _pill()]
    assert out["rich_content"] == {"sources": [_BASE]} and out["thinking"] == {"source_count": 1}
    assert msg["sources"] == [_BASE], "the input is not mutated"
    assert ws._overlay_live_sources(msg, None)["sources"] is None


# ── tool_step.skipped ────────────────────────────────────────────────────────


def _ok():
    return {"web_search": True, "status": "ok", "result_count": 1,
            "results": [{"n": 1, "publisher": "Reuters", "title": "t", "snippet": "s"}]}


@pytest.mark.parametrize("result,skipped", [
    (_ok(), False),
    ({"web_search": True, "status": "no_results", "result_count": 0, "results": []}, False),
    ({"web_search": True, "status": "daily_limit", "result_count": 0, "results": []}, True),
    ({"web_search": True, "status": "disabled", "result_count": 0, "results": []}, True),
    ({"web_search": True, "status": "unavailable", "error": "web search unavailable",
      "upstream": True, "result_count": 0, "results": []}, True),
    (dict(_ok(), repeat_note="one search per question"), False),       # a replay of a delivery
    ({"error": "invalid or missing web query", "note": "…"}, True),     # refused before search
    ({"error": "TimeoutError: timed out", "upstream": True}, True),     # the handler timed out
    ({"web_search": True, "status": "ok", "result_count": 0, "results": []}, True),  # malformed
    ({"web_search": True, "status": "something-new"}, True),
    (None, True), ("ok", True), ([], True),
])
def test_web_step_skipped(result, skipped):
    assert ws._web_step_skipped(result) is skipped


def test_skipped_agrees_with_the_service_outcomes():
    """Every outcome the service can produce, through its own `for_model`."""
    for status, expected in ((cws.STATUS_OK, False), (cws.STATUS_NO_RESULTS, False),
                             (cws.STATUS_DAILY_LIMIT, True), (cws.STATUS_DISABLED, True),
                             (cws.STATUS_UNAVAILABLE, True)):
        out = cws.WebSearchOutcome(status=status, query="q",
                                   results=[{"n": 1, "publisher": "Reuters", "title": "t",
                                             "published": None, "snippet": "s"}]
                                   if status == cws.STATUS_OK else [])
        for repeat in (False, True):
            assert ws._web_step_skipped(out.for_model(repeat=repeat)) is expected, status
            # The automatic tier's notes differ; the client-facing verdict never does.
            assert ws._web_step_skipped(out.for_model(repeat=repeat, tier=cws.TIER_AUTO)) is expected
    # A market-data query refused before any search (2026-10-08): no "Searching the web" claim.
    assert ws._web_step_skipped(cws._refused("Apple stock price")) is True
    # An automatic search deferred behind Caydex's own tools (2026-10-09): it did not run either.
    deferred = cws._deferred("Apple lawsuit")
    assert ws._web_step_skipped(deferred) is True and not cws.web_results_delivered(deferred)
    assert "error" not in deferred and "upstream" not in deferred, "never a refundable failure"


# ── end to end: pills the service builds pass the placement and the contract ──


def test_service_built_pills_validate_and_place_correctly(monkeypatch):
    monkeypatch.setattr(cws.settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 8000)
    raw = {"results": [
        {"title": "Apple <strong>DOJ</strong> case advances", "url": "https://www.reuters.com/legal/a",
         "description": "The case moved forward.", "page_age": "2026-09-30T08:00:00"},
        {"title": "Twin", "url": "https://reuters.com/other", "description": "Same publisher."},
        {"title": "AP", "url": "https://apnews.com/article/x", "description": "AP story.",
         "age": "2 days ago"},
        {"title": "Reddit", "url": "https://www.reddit.com/r/stocks", "description": "forum"},
        {"title": "Bad", "url": "javascript:alert(1)", "description": "x"},
    ]}
    outcome = cws._digest(raw, "Apple DOJ case", {"denied": 0, "invalid": 0})
    assert outcome.status == cws.STATUS_OK
    for pill in outcome.pills:
        ChatSourcePill.model_validate(pill)
        assert ws._web_pill_host(pill) is not None
    merged = ws._merge_web_pills([_BASE], outcome.pills)
    details = [p["detail"] for p in merged[1:]]
    # The `www.` twin the service keeps per HOST is folded here (a shipped build keys a pill on
    # `label|detail`, so two "Reuters" pills would collide).
    assert details == ["Reuters", "AP News"]
    assert merged[1]["title"] == "Apple DOJ case advances" and merged[1]["published_at"] == "2026-09-30"
    assert merged[2]["published_at"] is None, "a relative age is not a date"


def test_every_grounding_pill_also_fits_the_contract():
    for pill in (_BASE, _SCREEN, {"label": "Cay research report", "detail": None}):
        ChatSourcePill.model_validate(pill)
