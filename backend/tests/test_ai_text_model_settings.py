"""The ETF hook, the index stories, the crypto snapshots and the marketing writer read their Gemini
model (and crypto its thinking level) from settings, not from a hard-coded string (2026-11-16).

Why: Google routes every `gemini-2.5-flash` request to `gemini-3.5-flash-lite` from 2026-12-01, and
the owner's per-feature pick has to be a Railway variable change, not a deploy. Until 2026-11-16
each of these four calls named "gemini-2.5-flash" inline.

What is pinned:

* **Neutral by default.** Every model setting defaults to "gemini-2.5-flash" (the old string) and
  `CRYPTO_AI_THINKING_LEVEL` to None, and with those defaults each service's outgoing call carries
  EXACTLY the keyword set and values it carried before — no `thinking_level` key at all (an absent
  key, not `None`, so strict-signature fakes elsewhere keep working and the response-cache key is
  byte-identical). Through the REAL `GeminiClient`, the default crypto and ETF calls reach the SDK
  as before (crypto: no ThinkingConfig; ETF: budget 0 → 1 on 2.5-flash).
* **A set setting reaches the call**, read per call (two different values on one instance, two
  different models on the wire), and the ETF / index budget 0 stays.
* **Crypto's level** reaches the call for each of the four levels, without adding a budget; a blank
  or None level sends no key; on the real client a level reaches the SDK only for a model that
  accepts it (3.5-flash-lite), and is dropped for 2.5-flash (which refuses every level).
* **The writer** reads `MARKETING_WRITER_GEMINI_MODEL` once, at import (a fresh copy of the module
  executed under a patched setting carries it end to end: constant, the generate_json call and the
  stored package), and an already-imported module does not flip mid-process.

Hermetic: every Gemini object here is a fake; `backend/conftest.py` blocks sockets.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.config import Settings, settings

OLD_MODEL = "gemini-2.5-flash"
NEW_MODEL = "gemini-3.5-flash-lite"
OTHER_MODEL = "gemini-3.8-flash"

_SERVICE_SETTINGS = ("ETF_AI_MODEL", "INDEX_AI_MODEL", "CRYPTO_AI_MODEL", "CRYPTO_AI_THINKING_LEVEL")


# ── fakes and runners ─────────────────────────────────────────────────────────


class _RecordingGemini:
    """Records every generate_text call's kwargs; answers with an empty text (each service then
    takes its logged no-result branch and writes nothing that matters)."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def generate_text(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        return {"text": ""}


def _at_defaults(monkeypatch) -> None:
    """Pin every setting these services read to its DECLARED default, so a developer's
    backend/.env cannot make the neutrality test pass or fail by accident."""
    for name in _SERVICE_SETTINGS:
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)


async def _run_etf(monkeypatch, gem) -> None:
    import app.services.etf_service as mod

    svc = mod.ETFService.__new__(mod.ETFService)
    svc._tier2_put = lambda *a, **k: None                       # type: ignore[method-assign]
    monkeypatch.setattr(mod, "get_gemini_client", lambda: gem)
    monkeypatch.setattr(mod, "_cache_set", lambda *a, **k: None)
    await svc._refresh_hook_text(
        symbol="SCHD", name="Schwab U.S. Dividend Equity ETF", description="Dividend fund.",
        asset_class="Equity", index_tracked="Dow Jones U.S. Dividend 100", holdings_count=103,
        top_holdings=[], top_sectors=[], fallback="Tracks a dividend index of 103 companies.",
    )


async def _run_index(monkeypatch, gem) -> None:
    import app.services.index_service as mod
    from app.schemas.index import SectorPerformanceEntryResponse

    svc = mod.IndexService.__new__(mod.IndexService)
    svc._tier2_put = lambda *a, **k: None                       # type: ignore[method-assign]
    svc._upsert_macro_cache = lambda *a, **k: None              # type: ignore[method-assign]
    monkeypatch.setattr(mod, "get_gemini_client", lambda: gem)
    monkeypatch.setattr(mod, "_cache_set", lambda *a, **k: None)
    sectors = [
        SectorPerformanceEntryResponse(sector="Technology", change_percent=1.2),
        SectorPerformanceEntryResponse(sector="Utilities", change_percent=-0.3),
    ]
    await svc._refresh_ai_stories(
        symbol="^GSPC", sectors=sectors, has_macro=True,
        fallback=("{TOP_SECTOR} led.", "Macro template.", []),
    )


async def _run_crypto(monkeypatch, gem) -> None:
    import app.services.crypto_service as mod

    svc = mod.CryptoService.__new__(mod.CryptoService)
    svc._save_snapshots_db = lambda *a, **k: None               # type: ignore[method-assign]
    monkeypatch.setattr(mod, "get_gemini_client", lambda: gem)
    monkeypatch.setattr(mod, "_cache_set", lambda *a, **k: None)
    await svc._generate_ai_snapshots(
        symbol="BTCUSD", crypto_name="Bitcoin", profile_meta={}, cache_key="cache:BTCUSD",
    )


_RUNNERS = {"etf": (_run_etf, "ETF_AI_MODEL"), "index": (_run_index, "INDEX_AI_MODEL"),
            "crypto": (_run_crypto, "CRYPTO_AI_MODEL")}

#: The exact keyword set each call sent before 2026-11-16 (no thinking_level anywhere; crypto
#: sends no budget at all — its model thinks dynamically).
_TODAYS_KEYS = {
    "etf": {"prompt", "system_instruction", "model_name", "thinking_budget"},
    "index": {"prompt", "system_instruction", "model_name", "thinking_budget"},
    "crypto": {"prompt", "system_instruction", "model_name"},
}


async def _one_call(monkeypatch, which: str) -> Dict[str, Any]:
    gem = _RecordingGemini()
    await _RUNNERS[which][0](monkeypatch, gem)
    # An exception inside the service (e.g. a bad settings read) is swallowed into a WARNING and
    # leaves NO call — so the count is what proves the call happened at all.
    assert len(gem.calls) == 1, f"{which}: expected exactly one Gemini call, got {gem.calls}"
    return gem.calls[0]


# ── 1. neutral by default ─────────────────────────────────────────────────────


def test_the_declared_defaults_are_the_strings_that_were_hard_coded():
    for name in ("ETF_AI_MODEL", "INDEX_AI_MODEL", "CRYPTO_AI_MODEL", "MARKETING_WRITER_GEMINI_MODEL"):
        assert Settings.model_fields[name].default == OLD_MODEL, name
    assert Settings.model_fields["CRYPTO_AI_THINKING_LEVEL"].default is None


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(_RUNNERS))
async def test_default_settings_send_todays_exact_call(monkeypatch, which):
    _at_defaults(monkeypatch)
    call = await _one_call(monkeypatch, which)
    assert set(call) == _TODAYS_KEYS[which], sorted(call)
    assert call["model_name"] == OLD_MODEL
    if which in ("etf", "index"):
        assert call["thinking_budget"] == 0
    else:
        assert "thinking_budget" not in call
    assert "thinking_level" not in call, "an absent key, never thinking_level=None"


# ── 2. a set model reaches the call, read per call ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(_RUNNERS))
async def test_a_set_model_reaches_the_call_and_is_read_per_call(monkeypatch, which):
    _at_defaults(monkeypatch)
    _, setting = _RUNNERS[which]
    seen = []
    for model in (NEW_MODEL, OTHER_MODEL):            # two values, one process: never cached
        monkeypatch.setattr(settings, setting, model)
        call = await _one_call(monkeypatch, which)
        seen.append(call["model_name"])
        # The model is the only thing that moved: same keys as today, budget 0 kept.
        assert set(call) == _TODAYS_KEYS[which], sorted(call)
        if which in ("etf", "index"):
            assert call["thinking_budget"] == 0
    assert seen == [NEW_MODEL, OTHER_MODEL]


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(_RUNNERS))
async def test_each_service_reads_only_its_own_model_setting(monkeypatch, which):
    """Setting the OTHER two services' models leaves this one on the default."""
    _at_defaults(monkeypatch)
    for other, (_, setting) in _RUNNERS.items():
        if other != which:
            monkeypatch.setattr(settings, setting, NEW_MODEL)
    call = await _one_call(monkeypatch, which)
    assert call["model_name"] == OLD_MODEL


# ── 3. crypto's thinking level ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high"])
async def test_the_crypto_level_reaches_the_call_without_a_budget(monkeypatch, level):
    _at_defaults(monkeypatch)
    monkeypatch.setattr(settings, "CRYPTO_AI_MODEL", NEW_MODEL)
    monkeypatch.setattr(settings, "CRYPTO_AI_THINKING_LEVEL", level)
    call = await _one_call(monkeypatch, "crypto")
    assert call["thinking_level"] == level
    assert call["model_name"] == NEW_MODEL
    assert "thinking_budget" not in call, "crypto never sent a budget; a level must not add one"
    assert set(call) == _TODAYS_KEYS["crypto"] | {"thinking_level"}


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, ""])
async def test_a_blank_crypto_level_sends_no_level_key(monkeypatch, blank):
    """The config validator turns blank into None; even a raw "" (validator bypassed) must not
    reach the client as a level."""
    _at_defaults(monkeypatch)
    monkeypatch.setattr(settings, "CRYPTO_AI_THINKING_LEVEL", blank)
    call = await _one_call(monkeypatch, "crypto")
    assert "thinking_level" not in call
    assert set(call) == _TODAYS_KEYS["crypto"]


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["etf", "index"])
async def test_the_crypto_level_does_not_leak_into_the_etf_or_index_call(monkeypatch, which):
    _at_defaults(monkeypatch)
    monkeypatch.setattr(settings, "CRYPTO_AI_THINKING_LEVEL", "high")
    call = await _one_call(monkeypatch, which)
    assert "thinking_level" not in call and call["thinking_budget"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(_RUNNERS))
async def test_every_kwarg_binds_to_the_real_generate_text(monkeypatch, which):
    """The recording fake takes **kwargs; the real method must accept exactly what is sent,
    with every setting turned on."""
    from app.integrations.gemini import GeminiClient

    _at_defaults(monkeypatch)
    for name in ("ETF_AI_MODEL", "INDEX_AI_MODEL", "CRYPTO_AI_MODEL"):
        monkeypatch.setattr(settings, name, NEW_MODEL)
    monkeypatch.setattr(settings, "CRYPTO_AI_THINKING_LEVEL", "low")
    call = dict(await _one_call(monkeypatch, which))
    inspect.signature(GeminiClient.generate_text).bind(object(), **call)


# ── 4. through the REAL GeminiClient to the SDK ───────────────────────────────


def _real_client(sdk_calls: list):
    """A real GeminiClient whose SDK is a recorder (pattern from test_detail_ai_never_blocks)."""
    from app.integrations.gemini import GeminiClient, _TTLCache

    class _Models:
        async def generate_content(self, **kwargs):
            sdk_calls.append(kwargs)
            await asyncio.sleep(0)

            class _R:
                text = ""
                candidates: list = []
                usage_metadata = None
            return _R()

    class _Aio:
        models = _Models()

    class _Client:
        aio = _Aio()

    client = GeminiClient.__new__(GeminiClient)
    client.model_name = "client-default-model"
    client._temperature = 0.7
    client._max_tokens = 8192
    client._response_cache = _TTLCache(max_size=16, ttl_seconds=3600)
    client._embedding_cache = _TTLCache(max_size=16, ttl_seconds=3600)
    client._client = _Client()
    return client


async def _sdk_call(monkeypatch, which: str) -> Dict[str, Any]:
    sdk_calls: list = []
    client = _real_client(sdk_calls)
    await _RUNNERS[which][0](monkeypatch, client)
    assert len(sdk_calls) == 1, f"{which}: expected one SDK call, got {len(sdk_calls)}"
    return sdk_calls[0]


@pytest.mark.asyncio
async def test_real_client_default_crypto_call_reaches_the_sdk_as_before(monkeypatch):
    _at_defaults(monkeypatch)
    sent = await _sdk_call(monkeypatch, "crypto")
    assert sent["model"] == OLD_MODEL
    assert sent["config"].thinking_config is None, "today's crypto call carries no ThinkingConfig"


@pytest.mark.asyncio
async def test_real_client_default_etf_call_reaches_the_sdk_as_before(monkeypatch):
    from app.integrations.gemini import _BUDGET_OFF_ON_REFUSING_MODELS

    _at_defaults(monkeypatch)
    sent = await _sdk_call(monkeypatch, "etf")
    assert sent["model"] == OLD_MODEL
    tc = sent["config"].thinking_config
    assert tc.thinking_budget == _BUDGET_OFF_ON_REFUSING_MODELS and tc.thinking_level is None


@pytest.mark.asyncio
async def test_real_client_crypto_level_reaches_the_sdk_on_a_model_that_takes_it(monkeypatch):
    from google.genai import types

    _at_defaults(monkeypatch)
    monkeypatch.setattr(settings, "CRYPTO_AI_MODEL", NEW_MODEL)
    monkeypatch.setattr(settings, "CRYPTO_AI_THINKING_LEVEL", "low")
    sent = await _sdk_call(monkeypatch, "crypto")
    assert sent["model"] == NEW_MODEL
    tc = sent["config"].thinking_config
    assert tc.thinking_level == types.ThinkingLevel.LOW and tc.thinking_budget is None


@pytest.mark.asyncio
async def test_real_client_crypto_level_is_dropped_on_a_model_that_refuses_levels(monkeypatch):
    """Outlier: the level is set BEFORE the model is switched (still 2.5-flash, which 400s on any
    level). The wire must stay today's: no ThinkingConfig at all."""
    _at_defaults(monkeypatch)
    monkeypatch.setattr(settings, "CRYPTO_AI_THINKING_LEVEL", "medium")
    sent = await _sdk_call(monkeypatch, "crypto")
    assert sent["model"] == OLD_MODEL
    assert sent["config"].thinking_config is None


@pytest.mark.asyncio
async def test_real_client_etf_budget_zero_goes_out_as_minimal_on_flash_lite(monkeypatch):
    """The ETF hook keeps budget 0; naming 3.5-flash-lite turns that into its real off."""
    from google.genai import types

    _at_defaults(monkeypatch)
    monkeypatch.setattr(settings, "ETF_AI_MODEL", NEW_MODEL)
    sent = await _sdk_call(monkeypatch, "etf")
    assert sent["model"] == NEW_MODEL
    tc = sent["config"].thinking_config
    assert tc.thinking_level == types.ThinkingLevel.MINIMAL and tc.thinking_budget is None


# ── 5. the marketing writer: read at import ───────────────────────────────────

_WRITER_PATH = Path(__file__).resolve().parent.parent / "app" / "services" / "marketing" / "writer_service.py"


def _fresh_writer_module(monkeypatch, name: str = "_writer_service_settings_probe"):
    """Execute writer_service.py again as a separate module (never replacing the real one, whose
    classes other modules hold), so its import-time read sees the patched setting. Registered in
    sys.modules for the dataclass machinery; monkeypatch removes it afterwards."""
    spec = importlib.util.spec_from_file_location(name, _WRITER_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_the_writer_constant_is_the_setting():
    from app.services.marketing import writer_service as ws

    assert ws.WRITER_MODEL == settings.MARKETING_WRITER_GEMINI_MODEL
    assert ws.WriterResult.__dataclass_fields__["model"].default == ws.WRITER_MODEL


def test_the_writer_reads_its_model_at_import_not_mid_process(monkeypatch):
    from app.services.marketing import writer_service as ws

    before = ws.WRITER_MODEL
    monkeypatch.setattr(settings, "MARKETING_WRITER_GEMINI_MODEL", NEW_MODEL)
    assert ws.WRITER_MODEL == before, "an imported writer must not flip mid-process"
    probe = _fresh_writer_module(monkeypatch)
    assert probe.WRITER_MODEL == NEW_MODEL
    assert probe.WriterResult.__dataclass_fields__["model"].default == NEW_MODEL


@pytest.mark.asyncio
async def test_the_writer_setting_reaches_the_generate_json_call_and_the_package(monkeypatch):
    """End to end on a fresh copy of the module: setting → constant → the call → the stored
    package. The existing writer tests compare the call to `ws.WRITER_MODEL`, which cannot tell
    the constant from an inline "gemini-2.5-flash" while both hold the default."""
    import test_marketing_writer as tmw
    from app.services.marketing import post_copy

    monkeypatch.setattr(settings, "MARKETING_WRITER_GEMINI_MODEL", NEW_MODEL)
    probe = _fresh_writer_module(monkeypatch)
    client = tmw.FakeClient(tmw._result(tmw._clean_package()))
    res = await probe.generate_package(
        tmw._item(), tmw.TEMPLATE, tmw.RUN_DATE, generation_id="gen-settings-probe",
        client=client, allow_x_url=False, store_state=post_copy.STORE_PRELAUNCH,
        judge_mode=tmw.JUDGE_OFF, image_posts=True,
    )
    assert len(client.calls) == 1
    assert client.calls[0]["model_name"] == NEW_MODEL
    assert client.calls[0]["thinking_budget"] == probe.WRITER_THINKING_BUDGET == 0
    assert "thinking_level" not in client.calls[0]
    assert res.status == "accepted"
    assert res.model == NEW_MODEL and res.package["model"] == NEW_MODEL
