"""Thinking levels for the web-only Insights grounding judge and the theme insights summary
(2026-11-16, ahead of Google's 2026-12-01 reroute of gemini-2.5-flash to gemini-3.5-flash-lite).

Two settings, each read at CALL time:
* `INSIGHT_WEB_ONLY_JUDGE_THINKING_LEVEL` → the judge's `generate_json`
  (`news_insight_service._judge_web_only`, model `WEB_ONLY_JUDGE_MODEL`);
* `THEME_INSIGHTS_THINKING_LEVEL` → the theme summary's `generate_json`
  (`theme_insights_service._resolve_summary`), beside `THEME_INSIGHTS_THINKING_BUDGET`,
  which still goes out: the encoder sends the level to a model that takes one and the
  budget to any other, never both.

BEHAVIOR-NEUTRAL by default: unset, neither call gains a keyword, so a fake with the OLD
explicit signature still works, the wire request is the same and the judge's response-cache
key is byte-identical. Three layers: the keyword each service passes (strict old-signature
fakes + recording fakes), the wire through the REAL GeminiClient with a recording fake SDK,
and the settings' own validation. Hermetic: no network, no Supabase.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.integrations import gemini as gem
from app.services import news_insight_service as nis
from app.services import theme_insights_service as tis
from app.services.news_insight_service import NewsInsightService, _web_rows
from app.utils.market_hours import ET

JUDGE_SETTING = "INSIGHT_WEB_ONLY_JUDGE_THINKING_LEVEL"
THEME_SETTING = "THEME_INSIGHTS_THINKING_LEVEL"
LEVELS = ("minimal", "low", "medium", "high")


@pytest.fixture(autouse=True)
def _unset_levels(monkeypatch):
    """Pin the unset state whatever the local environment says, and the theme model/budget
    to their defaults, so every test starts from today's request."""
    monkeypatch.setattr(settings, JUDGE_SETTING, None)
    monkeypatch.setattr(settings, THEME_SETTING, None)
    monkeypatch.setattr(settings, "THEME_INSIGHTS_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "THEME_INSIGHTS_THINKING_BUDGET", 512)
    gem._quota_circuit.record_success()
    yield


# ══ 0. The settings themselves ═══════════════════════════════════════════════════════


def test_both_levels_default_to_unset():
    assert Settings.model_fields[JUDGE_SETTING].default is None
    assert Settings.model_fields[THEME_SETTING].default is None


@pytest.mark.parametrize("name", [JUDGE_SETTING, THEME_SETTING])
@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("   ", None),
    ("LOW", "low"), (" Minimal ", "minimal"), ("medium", "medium"), ("high", "high"),
])
def test_a_level_setting_is_normalised(name, raw, expected):
    assert getattr(Settings(**{name: raw}), name) == expected


@pytest.mark.parametrize("name", [JUDGE_SETTING, THEME_SETTING])
@pytest.mark.parametrize("raw", ["max", "off", "none", "0", "lowest", 1, True])
def test_a_level_typo_fails_the_deploy(name, raw):
    with pytest.raises(ValidationError):
        Settings(**{name: raw})


# ══ 1. The web-only grounding judge ══════════════════════════════════════════════════

NOW = datetime(2026, 11, 12, 18, 45, tzinfo=timezone.utc)
PUBLISHED = datetime(2026, 11, 12, 16, 52, tzinfo=timezone.utc)
DUBAI = {
    "title": "Joby delays its Dubai air taxi launch to 2027",
    "snippet": "Joby Aviation said certification work with the GCAA pushed its commercial "
               "launch in Dubai to 2027 and cut its 2026 delivery target to 4 aircraft.",
    "url": "https://www.reuters.com/business/aerospace/joby-dubai-delay-2026-11-12/",
    "publisher": "Reuters", "published_at": PUBLISHED,
}
HONEST_POINT = "Joby said certification work pushed its Dubai launch to 2027."
TAKEAWAY = "The Dubai launch now turns on certification work."
CARD = {"headline": "Joby delays its Dubai launch to 2027", "points": [HONEST_POINT],
        "sentiment": "bearish", "conclusion": TAKEAWAY}
#: The judge call's keywords before thinking levels existed.
JUDGE_KEYS = {"prompt", "system_instruction", "model_name", "response_schema", "temperature",
              "usage_tag"}


def _judge_ids(prompt: str) -> List[str]:
    return re.findall(r"^(H|P\d+|C): ", prompt.split("The brief (one line per id):", 1)[-1], re.M)


def _all_supported(prompt: str, bad=frozenset()) -> str:
    return json.dumps({"verdicts": [{"id": i, "supported": i not in bad}
                                    for i in _judge_ids(prompt)]})


class _OldJudgeGemini:
    """The judge call's signature BEFORE thinking levels: any new keyword is a TypeError,
    which `_judge_web_only` swallows into a None verdict — so a non-None verdict proves the
    call carried nothing new."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def generate_json(self, *, prompt, system_instruction=None, model_name=None,
                            response_schema=None, temperature=None, usage_tag=None):
        self.calls.append(dict(prompt=prompt, system_instruction=system_instruction,
                               model_name=model_name, response_schema=response_schema,
                               temperature=temperature, usage_tag=usage_tag))
        return {"text": _all_supported(prompt)}


class _ScriptedGemini:
    """Records every call; answers card, repair and judge calls from a script."""

    def __init__(self, card=CARD, repairs=(), judge=(frozenset(),)):
        self.card = card
        self.repairs = list(repairs)
        self.judge = list(judge)
        self.calls: List[Dict[str, Any]] = []

    async def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        tag = kwargs.get("usage_tag")
        if tag == nis._USAGE_TAG_JUDGE:
            bad = self.judge.pop(0) if len(self.judge) > 1 else self.judge[0]
            return {"text": _all_supported(kwargs["prompt"], bad)}
        if tag == nis._USAGE_TAG_REPAIR:
            answer = self.repairs.pop(0) if self.repairs else None
            return {"text": json.dumps({"conclusion": answer} if answer else {})}
        return {"text": json.dumps(self.card)}

    def tagged(self, tag):
        return [c for c in self.calls if c.get("usage_tag") == tag]


class _Svc(NewsInsightService):
    def __init__(self, gemini=None):
        self.supabase = None
        self._cache = {}
        self._inflight = {}
        self.gemini = gemini
        self.store_args = None

    def _store(self, *args, **kwargs):
        self.store_args = (args, kwargs)
        return True


async def _judge(svc):
    return await svc._judge_web_only("JOBY", CARD, _web_rows([DUBAI]), now=NOW)


async def _run(svc):
    return await svc.generate_and_store(
        scope="JOBY", corpus=[], inputset_id="iid", price_band="Unusual",
        trigger_reason="web_only (1)", quote=None, market_active=True, now=NOW,
        company_name="Joby Aviation, Inc.", web_context=[DUBAI], jumping=True,
    )


@pytest.mark.asyncio
async def test_judge_default_sends_exactly_todays_keywords():
    fake = _OldJudgeGemini()
    verdict = await _judge(_Svc(fake))
    assert verdict == (True, [True], True)          # None would mean a TypeError was swallowed
    (call,) = fake.calls
    assert set(call) == JUDGE_KEYS
    assert call["model_name"] == nis.WEB_ONLY_JUDGE_MODEL
    assert call["temperature"] == 0.0 and call["usage_tag"] == nis._USAGE_TAG_JUDGE


@pytest.mark.asyncio
@pytest.mark.parametrize("level", LEVELS)
async def test_judge_level_reaches_the_call(monkeypatch, level):
    fake = _ScriptedGemini()
    svc = _Svc(fake)
    monkeypatch.setattr(settings, JUDGE_SETTING, level)
    assert await _judge(svc) == (True, [True], True)
    (call,) = fake.calls
    assert call["thinking_level"] == level
    assert set(call) == JUDGE_KEYS | {"thinking_level"}       # no budget, nothing else new
    assert call["model_name"] == nis.WEB_ONLY_JUDGE_MODEL


@pytest.mark.asyncio
async def test_judge_reads_the_level_at_call_time(monkeypatch):
    fake = _ScriptedGemini()
    svc = _Svc(fake)
    await _judge(svc)
    monkeypatch.setattr(settings, JUDGE_SETTING, "low")
    await _judge(svc)
    monkeypatch.setattr(settings, JUDGE_SETTING, None)
    await _judge(svc)
    assert ["thinking_level" in c for c in fake.calls] == [False, True, False]
    assert fake.calls[1]["thinking_level"] == "low"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", None])
async def test_judge_blank_level_sends_no_keyword(monkeypatch, blank):
    """A value that bypassed the validator (monkeypatch, a future caller) still adds nothing."""
    monkeypatch.setattr(settings, JUDGE_SETTING, blank)
    fake = _OldJudgeGemini()
    assert await _judge(_Svc(fake)) == (True, [True], True)


@pytest.mark.asyncio
async def test_the_level_reaches_every_judge_call_and_no_other_call(monkeypatch):
    """A flagged takeaway is repaired and RE-JUDGED: both judge calls carry the level; the
    card writer and the repair (other models, other settings) never do."""
    monkeypatch.setattr(settings, JUDGE_SETTING, "medium")
    fake = _ScriptedGemini(repairs=["Certification work now sets the Dubai timing."],
                           judge=({"C"}, frozenset()))
    svc = _Svc(fake)
    card = await _run(svc)
    assert card is not None and svc.store_args is not None
    judges = fake.tagged(nis._USAGE_TAG_JUDGE)
    assert len(judges) == 2
    assert [c.get("thinking_level") for c in judges] == ["medium", "medium"]
    others = [c for c in fake.calls if c.get("usage_tag") != nis._USAGE_TAG_JUDGE]
    assert others and fake.tagged(nis._USAGE_TAG_CARD) and fake.tagged(nis._USAGE_TAG_REPAIR)
    assert not any("thinking_level" in c for c in others)


@pytest.mark.asyncio
async def test_default_full_web_only_card_carries_no_level_anywhere():
    fake = _ScriptedGemini()
    svc = _Svc(fake)
    assert await _run(svc) is not None
    assert fake.tagged(nis._USAGE_TAG_JUDGE)
    assert not any("thinking_level" in c for c in fake.calls)


# ── through the REAL GeminiClient: what goes on the wire ─────────────────────────────


class _Part:
    thought = False
    function_call = None

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.candidates = [type("C", (), {"content": type("Ct", (), {"parts": [_Part(text)]})(),
                                          "finish_reason": "STOP"})()]
        self.usage_metadata = None
        self.text = text


def _real_client(answer: Callable[[str], str], model: str = "gemini-2.5-flash"):
    """A real GeminiClient over a recording fake SDK (the pattern of
    test_gemini_budget_zero_refused.py). `answer(prompt)` is the model's text."""
    seen: List[dict] = []

    class _Models:
        async def generate_content(self, **kw):
            seen.append(kw)
            return _Resp(answer(kw["contents"]))

    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = model
    c._temperature, c._max_tokens = 0.7, settings.GEMINI_MAX_TOKENS
    c._response_cache = gem._TTLCache(max_size=16, ttl_seconds=3600)
    c._embedding_cache = gem._TTLCache(max_size=16, ttl_seconds=3600)
    c._client = type("Cl", (), {"aio": type("Aio", (), {"models": _Models()})()})()
    return c, seen


def _wire(kw: dict):
    """The thinking setting on the wire: None (no config), ("level", x) or ("budget", n).
    Never both (a 400 on every model)."""
    tc = kw["config"].thinking_config
    if tc is None:
        return None
    assert tc.thinking_budget is None or tc.thinking_level is None, "budget AND level is a 400"
    if tc.thinking_level is not None:
        return ("level", tc.thinking_level)
    return ("budget", tc.thinking_budget)


L = gem.types.ThinkingLevel

# (judge model, level setting, expected wire). The judge sends no budget of its own.
_JUDGE_WIRE = [
    ("gemini-2.5-flash", None, None),                       # today: no thinking config at all
    ("gemini-2.5-flash", "low", None),                      # 2.5-flash takes no level: ignored
    ("gemini-2.5-flash", "minimal", ("budget", 1)),         # minimal = off = its smallest budget
    ("gemini-3.5-flash-lite", None, None),                  # the model alone adds nothing
    ("gemini-3.5-flash-lite", "minimal", ("level", L.MINIMAL)),
    ("gemini-3.5-flash-lite", "medium", ("level", L.MEDIUM)),
    ("gemini-3.8-flash", "high", ("level", L.HIGH)),
    ("gemini-3.8-flash", "minimal", ("budget", 0)),         # 3.8 refuses minimal; takes 0
]


@pytest.mark.asyncio
@pytest.mark.parametrize("model,level,expected", _JUDGE_WIRE)
async def test_judge_wire_through_the_real_client(monkeypatch, model, level, expected):
    monkeypatch.setattr(nis, "WEB_ONLY_JUDGE_MODEL", model)
    monkeypatch.setattr(settings, JUDGE_SETTING, level)
    client, seen = _real_client(_all_supported)
    assert await _judge(_Svc(client)) == (True, [True], True)
    (kw,) = seen
    assert kw["model"] == model
    assert _wire(kw) == expected


@pytest.mark.asyncio
async def test_judge_default_response_cache_key_is_unchanged(monkeypatch):
    """Unset, the judge's verdict is cached under the key the client computed before thinking
    levels existed — no `tl=` part — so a deploy reuses nothing wrongly and misses nothing."""
    client, seen = _real_client(_all_supported)
    svc = _Svc(client)
    await _judge(svc)
    (kw,) = seen
    old_key = gem._cache_key(
        "json", kw["contents"], nis._JUDGE_SYSTEM_INSTRUCTION, nis.WEB_ONLY_JUDGE_MODEL,
        f"schema={nis._JUDGE_SCHEMA!r}", f"temp={0.0!r}",
    )
    assert client._response_cache.get(old_key) is not None
    await _judge(svc)                                   # served from that entry
    assert len(seen) == 1
    # A level set later never reuses the verdict cached without it.
    monkeypatch.setattr(nis, "WEB_ONLY_JUDGE_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(settings, JUDGE_SETTING, "low")
    await _judge(svc)
    assert len(seen) == 2 and _wire(seen[1]) == ("level", L.LOW)


# ══ 2. Theme insights summary ════════════════════════════════════════════════════════

AS_OF = date(2026, 9, 23)                                   # a Wednesday
THEME_NOW = datetime(2026, 9, 23, 22, 15, tzinfo=timezone.utc)
THEME = tis.ThemeSpec("alpha", "Alpha Rush", "AI", ("AAA", "BBB"))
THEME_KEYS = {"prompt", "system_instruction", "model_name", "response_schema",
              "thinking_budget", "usage_tag"}
SUMMARY = json.dumps({
    "headline": "Chip names moved on supply news",
    "summary": "The basket gained after several companies reported new orders.",
    "drivers": [{"ticker": "AAA", "note": "Reported a new supply agreement."}],
})


def _news(symbols_csv: str) -> List[Dict[str, Any]]:
    rows = []
    for s in symbols_csv.split(","):
        for k in range(2):
            rows.append({
                "symbol": s,
                "publishedDate": (THEME_NOW - timedelta(hours=4 + k)).astimezone(ET)
                .strftime("%Y-%m-%d %H:%M:%S"),
                "title": f"{s} reports update {k}",
                "text": f"{s} said something happened ({k}).",
                "url": f"https://news.example/{s}/{k}",
                "publisher": "Wire",
            })
    return rows


class _FMP:
    async def get_stock_news(self, ticker=None, limit=10, from_date=None, to_date=None, page=0):
        return _news(ticker)


class _OldThemeGemini:
    """The theme call's signature BEFORE thinking levels (the one in
    test_theme_insights_service.py): any new keyword is a TypeError."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def generate_json(self, prompt, system_instruction=None, model_name=None,
                            response_schema=None, thinking_budget=None, usage_tag=None):
        self.calls.append(dict(prompt=prompt, system_instruction=system_instruction,
                               model_name=model_name, response_schema=response_schema,
                               thinking_budget=thinking_budget, usage_tag=usage_tag))
        return {"text": SUMMARY, "tokens_used": 321}


class _RecordingThemeGemini:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def generate_json(self, prompt, **kwargs):
        self.calls.append(dict(prompt=prompt, **kwargs))
        return {"text": SUMMARY, "tokens_used": 321}


async def _summarize(gemini):
    svc = tis.ThemeInsightsService(supabase=object(), fmp=_FMP(), gemini=gemini)
    stats = tis._RunStats()
    perf = tis.ThemePerformance(usable=True, reason=None, day_change_pct=1.5,
                                day_moves={"AAA": 1.0, "BBB": 2.0})
    out = await svc._resolve_summary(THEME, perf, None, AS_OF, THEME_NOW, stats)
    return out, stats


@pytest.mark.asyncio
async def test_theme_default_sends_exactly_todays_keywords():
    fake = _OldThemeGemini()
    out, stats = await _summarize(fake)
    # A TypeError would show as a generation failure and an absent summary.
    assert stats.generation_failures == 0 and stats.generated == 1
    assert out["summary_text"] and out["model"] == "gemini-2.5-flash"
    (call,) = fake.calls
    assert set(call) == THEME_KEYS
    assert call["model_name"] == "gemini-2.5-flash" and call["thinking_budget"] == 512
    assert call["usage_tag"] == "theme_insights"


@pytest.mark.asyncio
@pytest.mark.parametrize("level", LEVELS)
async def test_theme_level_reaches_the_call_beside_the_budget(monkeypatch, level):
    monkeypatch.setattr(settings, THEME_SETTING, level)
    fake = _RecordingThemeGemini()
    out, stats = await _summarize(fake)
    assert stats.generated == 1 and out["summary_text"]
    (call,) = fake.calls
    assert call["thinking_level"] == level
    assert call["thinking_budget"] == 512                 # stays: the encoder picks one
    assert set(call) == THEME_KEYS | {"thinking_level"}


@pytest.mark.asyncio
async def test_theme_reads_the_level_at_call_time(monkeypatch):
    fake = _RecordingThemeGemini()
    await _summarize(fake)
    monkeypatch.setattr(settings, THEME_SETTING, "high")
    await _summarize(fake)
    monkeypatch.setattr(settings, THEME_SETTING, "")       # bypassed the validator: still nothing
    await _summarize(fake)
    assert [c.get("thinking_level") for c in fake.calls] == [None, "high", None]
    assert ["thinking_level" in c for c in fake.calls] == [False, True, False]


# (theme model, level setting, expected wire) with THEME_INSIGHTS_THINKING_BUDGET = 512.
_THEME_WIRE = [
    ("gemini-2.5-flash", None, ("budget", 512)),             # today
    ("gemini-2.5-flash", "low", ("budget", 512)),            # level refused there: budget rules
    ("gemini-2.5-flash", "minimal", ("budget", 1)),          # minimal = off
    ("gemini-3.5-flash-lite", None, ("budget", 512)),        # no level: the budget, as today
    ("gemini-3.5-flash-lite", "low", ("level", L.LOW)),      # level alone, no budget
    ("gemini-3.5-flash-lite", "minimal", ("level", L.MINIMAL)),
    ("gemini-3.8-flash", "high", ("level", L.HIGH)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("model,level,expected", _THEME_WIRE)
async def test_theme_wire_through_the_real_client(monkeypatch, model, level, expected):
    monkeypatch.setattr(settings, "THEME_INSIGHTS_MODEL", model)
    monkeypatch.setattr(settings, THEME_SETTING, level)
    client, seen = _real_client(lambda prompt: SUMMARY)
    out, stats = await _summarize(client)
    assert stats.generated == 1 and out["model"] == model
    (kw,) = seen
    assert kw["model"] == model
    assert _wire(kw) == expected
