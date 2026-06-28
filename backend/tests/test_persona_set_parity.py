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
