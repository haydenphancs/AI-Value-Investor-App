"""A curated crypto profile's `launch_date` is when the NETWORK went live, never a token sale.

The field is shown verbatim as the iOS profile's "Launch Date" row and is handed to the AI
snapshot prompt ("Launch date: …"), whose answer `crypto_snapshots` keeps PERMANENTLY. LINK
held its 2017 ICO date, so the stored "Origin and Technology" text said Chainlink launched in
2017 — 5 of the snapshots the judge flagged in the 2026-11-16 model eval. Chainlink's mainnet
went live on May 30, 2019. TRX ("September 2017", the ERC-20 token sale; mainnet May 31, 2018)
and MATIC ("April 26, 2019", the Binance Launchpad sale and listing; Matic PoS mainnet May 31,
2020) had the same mix-up.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.services import crypto_service
from app.services.crypto_service import _CRYPTO_PROFILES, CryptoService

# The network launches that replaced a token-sale date on 2026-11-19.
_FIXED = {
    "LINK": "May 30, 2019",   # Chainlink mainnet on Ethereum
    "MATIC": "May 31, 2020",  # Matic PoS mainnet (renamed Polygon, Feb 2021)
    "TRX": "May 31, 2018",    # TRON mainnet (Odyssey 2.0); genesis block June 25, 2018
}

# Token-event months that must never come back as a launch date: each of these coins sold or
# distributed its token in that month, months or years before its network went live.
_TOKEN_EVENT_MONTHS = {
    "LINK": {(2017, 9)},            # token sale
    "MATIC": {(2019, 4)},           # Binance Launchpad sale + first listing
    "TRX": {(2017, 8), (2017, 9)},  # ERC-20 token sale
    "DOT": {(2017, 10)},            # token sale
    "AVAX": {(2020, 7)},            # public token sale
    "ARB": {(2023, 3)},             # token airdrop
    "OP": {(2022, 5)},              # token airdrop
    "UNI": {(2020, 9)},             # token airdrop (the protocol launched in 2018)
}

_BTC_GENESIS = date(2009, 1, 3)


def _parse(value: str) -> date:
    """'Month D, YYYY' or 'Month YYYY' — the two shapes the table uses."""
    for fmt in ("%B %d, %Y", "%B %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise AssertionError(f"unparseable launch_date {value!r}")


@pytest.mark.parametrize("symbol,expected", sorted(_FIXED.items()))
def test_the_sale_dates_are_replaced_by_the_network_launch(symbol, expected):
    assert _CRYPTO_PROFILES[symbol]["launch_date"] == expected


@pytest.mark.parametrize("symbol,months", sorted(_TOKEN_EVENT_MONTHS.items()))
def test_no_profile_holds_a_token_event_month(symbol, months):
    launched = _parse(_CRYPTO_PROFILES[symbol]["launch_date"])
    assert (launched.year, launched.month) not in months, (
        f"{symbol}'s launch_date {launched} is a token-sale/airdrop month, not its network launch"
    )


@pytest.mark.parametrize("symbol", sorted(_CRYPTO_PROFILES))
def test_every_launch_date_is_a_readable_real_date(symbol):
    value = _CRYPTO_PROFILES[symbol].get("launch_date")
    assert isinstance(value, str) and value.strip() == value and value, (
        f"{symbol}: launch_date must be a non-empty, trimmed string (iOS shows it verbatim)"
    )
    launched = _parse(value)
    assert _BTC_GENESIS <= launched <= date.today(), (
        f"{symbol}: launch_date {launched} is before Bitcoin's genesis block or in the future"
    )


def test_the_table_parse_rejects_the_shapes_it_must():
    """Anti-vacuity for the format test above: a typo or an ISO date is not silently read."""
    for bad in ("Sept 2017", "2017-09-19", "", "Unknown", "May 30 2019"):
        with pytest.raises(AssertionError):
            _parse(bad)


class _RecordingGemini:
    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    async def generate_text(self, *, prompt, **_kwargs):
        self.prompts.append(prompt)
        return {"text": self.text}


_FOUR_SECTIONS = "".join(
    f"===CATEGORY==={name}\nA paragraph long enough to be kept by the parser for {name}.\n\n"
    for name in ("Origin and Technology", "Tokenomics", "Next Big Moves", "Risks")
)


@pytest.mark.asyncio
async def test_the_snapshot_prompt_carries_the_mainnet_date_not_the_sale(monkeypatch):
    """End to end through the prompt the stored text is written from."""
    gem = _RecordingGemini(_FOUR_SECTIONS)
    monkeypatch.setattr(crypto_service, "get_gemini_client", lambda: gem)
    monkeypatch.setattr(crypto_service, "_cache_set", lambda *a, **k: None)
    saved: list = []
    svc = CryptoService.__new__(CryptoService)
    monkeypatch.setattr(svc, "_save_snapshots_db", lambda sym, snaps: saved.append((sym, snaps)))

    await svc._generate_ai_snapshots(
        symbol="LINK",
        crypto_name="Chainlink",
        profile_meta=_CRYPTO_PROFILES["LINK"],
        cache_key="test_crypto_snapshots_LINK",
    )

    assert len(gem.prompts) == 1
    assert "- Launch date: May 30, 2019\n" in gem.prompts[0]
    assert "2017" not in gem.prompts[0]
    assert saved and saved[0][0] == "LINK" and len(saved[0][1]) == 4, (
        "the fake answer did not reach the save — the prompt assertion would prove nothing"
    )


def test_the_template_fallback_reads_the_mainnet_date():
    svc = CryptoService.__new__(CryptoService)
    origin = svc._default_snapshots("LINK", "Chainlink", _CRYPTO_PROFILES["LINK"])[0]
    assert origin.category == "Origin and Technology"
    assert "Launched on May 30, 2019," in " ".join(origin.paragraphs)
