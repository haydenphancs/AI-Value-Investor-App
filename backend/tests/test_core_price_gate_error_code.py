"""A first-paint price gap is a RETRYABLE upstream gap, not a report-generation failure.

`get_etf_core` and `get_crypto_core` refuse to paint "$0.00" under a live badge — correct.
But they raised a bare `ValueError`, and `classify_exception` only maps a ValueError whose
message contains "profile" (→ TICKER_NOT_FOUND). Everything else fell through to
`REPORT_GENERATION_FAILED`, so the ETF and crypto detail screens reported a research-report
failure for a missing quote, and the user got "Something went wrong" instead of a retry
(found 2026-09-12). `index_service` and `commodity_service` already raise the typed
exception for the identical condition.
"""

from __future__ import annotations

import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.integrations.fmp import FMPUnavailableException


def test_the_typed_exception_maps_to_a_retryable_upstream_code():
    code, status = classify_exception(
        FMPUnavailableException("ETF core has no usable price for SPY")
    )
    assert code is ErrorCode.FMP_UNAVAILABLE
    assert status in (502, 503)


def test_a_bare_value_error_would_still_be_misclassified():
    """Anti-vacuity: this is what the sites used to raise, and why it mattered."""
    code, _ = classify_exception(ValueError("ETF core has no usable price for SPY"))
    assert code is ErrorCode.REPORT_GENERATION_FAILED


@pytest.mark.parametrize("module,needle", [
    ("app/services/etf_service.py", "ETF core has no usable price"),
    ("app/services/crypto_service.py", "crypto core has no usable price"),
])
def test_the_core_gates_raise_the_typed_exception(module, needle):
    import re
    from pathlib import Path

    src = Path(module).read_text(encoding="utf-8")
    body = "\n".join(re.sub(r"#.*$", "", l) for l in src.splitlines())
    i = body.index(needle)
    raise_line = body[body.rindex("raise", 0, i):i]
    assert "FMPUnavailableException" in raise_line, (
        f"{module} still raises a bare ValueError for a missing first-paint price"
    )


def test_the_sibling_services_already_agreed():
    """The precedent this aligns with — if these change, re-read the decision."""
    import re
    from pathlib import Path

    for module, needle in (("app/services/commodity_service.py", "has no usable price"),
                           ("app/services/index_service.py", "FMPUnavailableException")):
        src = Path(module).read_text(encoding="utf-8")
        assert "FMPUnavailableException" in src, module
        assert re.search(needle, src), module
