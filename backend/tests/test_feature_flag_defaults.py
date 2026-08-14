"""The personalization flags must ship OFF, and nothing asserted that until now.

WHY THIS FILE EXISTS. Three flags gate a feature whose constraint is LEGAL, not technical
(`config.py`: "the live Terms §2 still promise output is 'general and impersonal'"). They
were written to default `False` and reviewed as defaulting `False` — but no test said so,
so a one-word edit, a merge, or a stray line in `.env` could have flipped a compliance
gate with a fully green suite.

WHY IT ASSERTS ON `Settings.model_fields`, NOT ON `settings`. `Settings` is a
`pydantic_settings.BaseSettings` reading a real `env_file` plus the real process
environment, and the module-level `settings` is that resolved singleton. An assertion
against the singleton tests THIS MACHINE'S environment, not the shipped default: it would
pass on a laptop with no override and keep passing after someone changed the declared
default, because a `.env` line would mask it. `model_fields[...].default` is the declared
value — the thing that ships — and it is unaffected by any environment.

So this file deliberately does NOT import `settings`. If a future edit makes it do so,
that is the bug this docstring exists to describe.
"""

from app.config import Settings

# Flag → why it must ship off. The reason is part of the assertion: a future reader
# deciding to flip a default should have to read the argument against it first.
_MUST_SHIP_OFF = {
    "CHAT_PERSONALIZATION_ENABLED": (
        "Turns output that is identical for every reader into output composed for one. "
        "Gated on the Terms §2 carve-out being live in the IN-APP mirror, not just the "
        "published HTML — that is the document a user reads before consenting."
    ),
    "CHAT_MEMORY_FACTS_ENABLED": (
        "Writes derived facts about a person across sessions. Cannot function without "
        "CHAT_PERSONALIZATION_ENABLED anyway (`_record_memory_facts` delegates to "
        "`may_apply_profile`), so enabling it alone is inert — and enabling it TOGETHER "
        "with personalization is a second, separate consent question."
    ),
    "CHAT_MODEL_ROUTING_ENABLED": (
        "The one cost lever that can change what a user reads. Wants the offline eval "
        "plus real answers reviewed first (scripts/eval_model_routing.py)."
    ),
}


def test_the_flags_still_exist():
    """Guard against the guard: a rename would make every assertion below vacuous."""
    missing = [name for name in _MUST_SHIP_OFF if name not in Settings.model_fields]
    assert not missing, (
        f"{missing} no longer exist on Settings — this file's guarantees are silently "
        f"gone. Rename them here too, or delete the entries deliberately."
    )


def test_personalization_flags_default_to_off():
    for name, why in _MUST_SHIP_OFF.items():
        default = Settings.model_fields[name].default
        assert default is False, (
            f"{name} declares default {default!r}, must be False.\n{why}\n"
            f"Enabling it is a runtime decision (a Railway variable), never a code default."
        )


def test_this_file_reads_the_declared_default_not_the_environment():
    """Pins the reasoning in the docstring so the file cannot quietly become useless.

    `model_fields[...].default` must stay independent of the environment. If someone
    'simplifies' this to `settings.CHAT_PERSONALIZATION_ENABLED`, the suite would start
    testing whoever ran it rather than what ships — and would go red on the very machine
    that legitimately enables the flag for a local drill.
    """
    import os

    key = "CHAT_PERSONALIZATION_ENABLED"
    original = os.environ.get(key)
    os.environ[key] = "true"
    try:
        assert Settings.model_fields[key].default is False, (
            "the declared default moved when an env var was set — this file is reading "
            "the environment, not the shipped default"
        )
    finally:
        if original is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original
