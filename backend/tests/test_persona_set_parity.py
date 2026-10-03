"""
Persona-set parity guard.

The valid-persona set is declared in two backend places that MUST agree:
  - persona_config.PERSONA_KEYS          (the research agent's registry gate)
  - ticker_report.VALID_PERSONAS         (the /stocks/{ticker}/report gate)

…and both must match the keys the iOS app ships in
`AnalysisPersona.allCases` (ResearchModels.swift):
    warren_buffett / cathie_wood / peter_lynch / bill_ackman / michael_burry

ticker_report.VALID_PERSONAS now aliases PERSONA_KEYS, so this test would
only fail if someone re-introduces a hand-maintained literal that drifts, or
adds a key to one side without the registry/iOS. A failure here means one
entry point accepts a persona the other rejects (or the agent has no config
for a key the UI offers) — fix the divergence, don't edit the expected set
without also updating iOS.
"""

import pytest

# The canonical keys shipped by the iOS persona picker. Keep in sync with
# AnalysisPersona.allCases in frontend/ios/ios/Models/ResearchModels.swift.
_IOS_PERSONA_KEYS = {"warren_buffett", "cathie_wood", "peter_lynch", "bill_ackman", "michael_burry"}


def test_persona_keys_match_ios_shipped_set():
    from app.services.agents.persona_config import PERSONA_KEYS

    assert PERSONA_KEYS == _IOS_PERSONA_KEYS


def test_ticker_report_valid_personas_equals_persona_keys():
    from app.api.v1.endpoints.ticker_report import VALID_PERSONAS
    from app.services.agents.persona_config import PERSONA_KEYS

    assert VALID_PERSONAS == PERSONA_KEYS


def test_agent_tag_round_trip_covers_every_persona():
    # The persona-key → badge-tag map (_AGENT_MAP) MUST cover every PERSONA_KEY with a
    # DISTINCT tag. A missing key makes collect() emit "buffett" (the .get default) for
    # that persona's report → a Buffett badge + "Value" lens on, e.g., a Burry/Contrarian
    # analysis. Assert against the LITERAL tags (not _AGENT_MAP.get(...)) so a dropped or
    # mistyped key fails loudly instead of collapsing both sides to "buffett".
    from app.services.agents.ticker_report_data_collector import _AGENT_MAP
    from app.services.agents.persona_config import PERSONA_KEYS

    assert set(_AGENT_MAP) == PERSONA_KEYS
    assert _AGENT_MAP["michael_burry"] == "burry"
    assert len(set(_AGENT_MAP.values())) == len(_AGENT_MAP)  # no two personas collide


def test_every_persona_key_has_a_registry_config():
    # get_persona_config falls back to Buffett on an unknown key. Ensure no
    # *valid* key ever hits that fallback (which would silently mis-score).
    from app.services.agents.persona_config import (
        PERSONA_KEYS,
        _PERSONA_REGISTRY,
        get_persona_config,
    )

    assert set(_PERSONA_REGISTRY) == PERSONA_KEYS
    for key in PERSONA_KEYS:
        assert get_persona_config(key) is _PERSONA_REGISTRY[key]


# ── Agent tag <-> persona key (persona_config.AGENT_TAG_TO_KEY) ───────────────
#
# One shared map replaces the private copies (the chat context resolver kept its own). It is
# DERIVED from PersonaConfig.agent_tag, so these pin the derivation, its inverse with the
# collector's `_AGENT_MAP`, and the closed, never-echoing resolver every caller goes through.


def test_agent_tag_map_is_derived_from_the_registry():
    from app.services.agents.persona_config import AGENT_TAG_TO_KEY, _PERSONA_REGISTRY

    assert AGENT_TAG_TO_KEY == {cfg.agent_tag: key for key, cfg in _PERSONA_REGISTRY.items()}
    assert len(AGENT_TAG_TO_KEY) == len(_PERSONA_REGISTRY), "two personas share an agent_tag"


def test_agent_tag_map_is_the_inverse_of_the_collectors_agent_map():
    from app.services.agents.persona_config import AGENT_TAG_TO_KEY
    from app.services.agents.ticker_report_data_collector import _AGENT_MAP

    assert {tag: key for key, tag in _AGENT_MAP.items()} == AGENT_TAG_TO_KEY


def test_agent_tag_map_holds_the_current_tags_only():
    """`dalio` (the pre-rename Activist tag frozen in old reports) is LEGACY: a caller that
    looks up today's report by tag must not treat it as bill_ackman."""
    from app.services.agents.persona_config import AGENT_TAG_TO_KEY, LEGACY_AGENT_TAGS

    assert set(AGENT_TAG_TO_KEY) == {"buffett", "wood", "lynch", "ackman", "burry"}
    assert LEGACY_AGENT_TAGS == {"dalio": "bill_ackman"}
    assert not set(LEGACY_AGENT_TAGS) & set(AGENT_TAG_TO_KEY)


def test_persona_key_from_tag_resolves_tags_and_keys_alike():
    from app.services.agents.persona_config import (
        AGENT_TAG_TO_KEY, PERSONA_KEYS, persona_key_from_tag,
    )

    for tag, key in AGENT_TAG_TO_KEY.items():
        assert persona_key_from_tag(tag) == key
        assert persona_key_from_tag(f"  {tag.upper()}\t") == key
    for key in PERSONA_KEYS:
        assert persona_key_from_tag(key) == key
        assert persona_key_from_tag(f" {key.upper()} ") == key


def test_persona_key_from_tag_takes_legacy_tags_only_when_asked():
    from app.services.agents.persona_config import persona_key_from_tag

    assert persona_key_from_tag("dalio") is None
    assert persona_key_from_tag(" DALIO ", include_legacy=True) == "bill_ackman"
    # Opting in to legacy never changes a current tag's answer.
    assert persona_key_from_tag("lynch", include_legacy=True) == "peter_lynch"



@pytest.mark.parametrize("value", [
    None, "", " ", "\t\n", 5, 5.0, True, [], {}, ("lynch",), object(), b"lynch",
    "soros", "warren", "buffett2", "lynch|", "AAPL|lynch", "peter lynch", "peter-lynch",
    "lynch\x00", "l" * 65, " " * 100 + "lynch", "x" * 10_000,
])
def test_persona_key_from_tag_is_closed(value):
    from app.services.agents.persona_config import persona_key_from_tag

    assert persona_key_from_tag(value) is None
    assert persona_key_from_tag(value, include_legacy=True) is None


def test_persona_key_from_tag_never_echoes_the_callers_string():
    """The result may reach a prompt; it must be OUR key object, never the input (a str
    subclass or a differently-cased copy carrying the caller's identity)."""
    from app.services.agents.persona_config import PERSONA_KEYS, persona_key_from_tag

    class Tainted(str):
        pass

    canonical = {k: k for k in PERSONA_KEYS}
    for raw in (Tainted("peter_lynch"), Tainted("lynch"), " Peter_Lynch "):
        out = persona_key_from_tag(raw)
        assert out == "peter_lynch"
        assert type(out) is str, "a str subclass carried the caller's object through"
        assert out is not raw
        assert out is canonical["peter_lynch"]


def test_every_persona_key_has_a_method_opening():
    from app.services.agents.persona_config import PERSONA_KEYS, PERSONA_METHODS

    assert set(PERSONA_METHODS) == PERSONA_KEYS


def test_every_persona_key_has_a_report_chat_voice():
    """The report chat's mode voice (agents/report_voice_prompt.py) is a closed registry keyed
    by PERSONA_KEYS: a sixth persona cannot ship with a report that its chat answers in the
    neutral register, and no voice can exist for a persona that does not."""
    from app.services.agents.persona_config import PERSONA_KEYS
    from app.services.agents.report_voice_prompt import REPORT_VOICE_KEYS

    assert REPORT_VOICE_KEYS == PERSONA_KEYS
