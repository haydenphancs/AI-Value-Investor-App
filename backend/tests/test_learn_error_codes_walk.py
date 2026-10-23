"""Every Learn weekly exception class classifies to its worker-facing LEARN_NARRATOR_* code.

`app/services/learn_weekly/errors.py` (plan 2026-10-22 §1.1/§1.8) holds the narrator-API family
(`LearnNarratorError` and its five subclasses) and the pipeline base `LearnWeeklyError`;
`app/api/error_response.classify_exception` maps them by class name along the MRO, ahead of every
heuristic. This mirrors `test_marketing_script_flow.py::test_every_marketing_exception_class_classifies_to_a_marketing_code`
and the worker-only pin in `test_marketing_run_service.py`: a class that misses its branch falls to
REPORT_GENERATION_FAILED, which the narrator worker would RETRY — and a "timeout" / "429" in its message
would be filed as an FMP outage.

Pinned here:
  * every class in errors.py → its exact (code, status), with heuristic-bait messages too; a NEW class in
    errors.py fails the walk until it is added to EXPECTED deliberately;
  * a subclass declared anywhere else inherits its parent's code (the MRO match), and a same-named class
    in another module is never taken for ours;
  * the LEARN_NARRATOR_* codes are exactly the worker-only list, each with copy and a status, and none has
    an iOS `AppError` branch (iOS never reaches /api/v1/internal/learn/*) — the AppError.swift scan strips
    comments first and is self-tested against a synthetic source both ways, so it cannot go vacuous;
  * the identifiers an exception carries: rendered, folded, capped, and never an unknown (maybe secret) key.

Mutation-checked by hand (2026-10-22, in-memory mutant copies only — no repo file touched): deleting the
`_learn_exception_code` branch from `classify_exception` turned the walk, the error-body, the subclass and
the loaded-subclass tests red; moving it BELOW the generic heuristics turned the bait-message cases and
both subclass tests red; a `"LEARN_NARRATOR_NOT_FOUND"` literal in the code of a copy of AppError.swift
fed to `_ios_mapped_codes` was flagged (the same literal inside a `//` comment was not); renaming
`LearnNarratorAssetMissing` in a copy of errors.py turned the walk-coverage and table-drift tests red.
"""

from __future__ import annotations

import inspect
import json
import logging
import pickle
import re
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from app.api import error_response as er
from app.api.error_response import ErrorCode, classify_exception, error_response_from_exception
from app.services.learn_weekly import errors

#: class name → (code, status). The contract the narrator worker is built against (plan §1.1).
EXPECTED: Dict[str, Tuple[str, int]] = {
    "LearnNarratorNotFound": ("LEARN_NARRATOR_NOT_FOUND", 404),
    "LearnNarratorJobNotHeld": ("LEARN_NARRATOR_JOB_NOT_HELD", 409),
    "LearnNarratorRequestInvalid": ("LEARN_NARRATOR_REQUEST_INVALID", 422),
    "LearnNarratorAssetMismatch": ("LEARN_NARRATOR_ASSET_MISMATCH", 409),
    "LearnNarratorAssetMissing": ("LEARN_NARRATOR_ASSET_MISSING", 409),
    "LearnNarratorLedgerError": ("LEARN_NARRATOR_LEDGER_ERROR", 503),
    "LearnNarratorError": ("LEARN_NARRATOR_LEDGER_ERROR", 503),
    "LearnWeeklyError": ("LEARN_NARRATOR_LEDGER_ERROR", 503),
}

#: The worker-only Learn codes: no iOS `AppError` branch BY DESIGN (marketing's WORKER_ONLY_ERROR_CODES).
WORKER_ONLY_LEARN_CODES = (
    "LEARN_NARRATOR_NOT_FOUND", "LEARN_NARRATOR_JOB_NOT_HELD", "LEARN_NARRATOR_REQUEST_INVALID",
    "LEARN_NARRATOR_ASSET_MISMATCH", "LEARN_NARRATOR_ASSET_MISSING", "LEARN_NARRATOR_LEDGER_ERROR",
)

#: Messages the generic heuristics would misfile: "quota" → GEMINI_QUOTA_EXCEEDED, "429"/"rate limit" →
#: FMP_RATE_LIMITED, "timeout" → FMP_UNAVAILABLE, "profile" in a ValueError → TICKER_NOT_FOUND.
_BAIT_MESSAGES = (
    "x",
    "",
    "upstream said 429 Too Many Requests (rate limit)",
    "storage read timeout after 30 s",
    "resource_exhausted: quota",
    "speaker profile not found",
)

_SWIFT_APP_ERROR = Path(__file__).resolve().parents[2] / "frontend/ios/ios/Core/Utilities/AppError.swift"


def _classes_defined_in_errors() -> List[type]:
    return [
        obj for _, obj in inspect.getmembers(errors, inspect.isclass)
        if issubclass(obj, BaseException) and obj.__module__ == errors.__name__
    ]


def _all_subclasses(base: type) -> List[type]:
    seen: List[type] = []
    stack = [base]
    while stack:
        for sub in stack.pop().__subclasses__():
            if sub not in seen:
                seen.append(sub)
                stack.append(sub)
    return seen


# ── test-local subclasses: declared OUTSIDE errors.py, with names the table does not know ─────────


class WriterCapsReached(errors.LearnWeeklyError):
    """A stage's own error (as writer.py would declare it): inherits LearnWeeklyError's code."""


class JobGone(errors.LearnNarratorJobNotHeld):
    """A narrower narrator refusal: inherits JOB_NOT_HELD, not the base's 503."""


class TimingTableBroken(errors.LearnNarratorRequestInvalid, ValueError):
    """Multiply inherits ValueError — the classifier's very first heuristic is a ValueError check."""


class LedgerTimeout(errors.LearnNarratorLedgerError, TimeoutError):
    """Multiply inherits TimeoutError, whose class name says "timeout"."""


_LOCAL_SUBCLASSES = {
    WriterCapsReached: "LearnWeeklyError",
    JobGone: "LearnNarratorJobNotHeld",
    TimingTableBroken: "LearnNarratorRequestInvalid",
    LedgerTimeout: "LearnNarratorLedgerError",
}


# ── the walk ───────────────────────────────────────────────────────────────────────────────────


def test_the_walk_covers_exactly_the_classes_errors_py_defines():
    """A new class in errors.py must be pinned here deliberately — never walked past."""
    names = sorted(c.__name__ for c in _classes_defined_in_errors())
    assert names == sorted(EXPECTED), "errors.py and EXPECTED disagree — pin the new class's (code, status)"
    assert len(names) == 8   # anti-vacuity: the walk below is not over an empty module


@pytest.mark.parametrize("message", _BAIT_MESSAGES)
def test_every_class_in_errors_py_classifies_to_its_learn_code(message):
    for cls in _classes_defined_in_errors():
        code, status = classify_exception(cls(message))
        assert (code.value, status) == EXPECTED[cls.__name__], (cls.__name__, message)
        assert code.value.startswith("LEARN_NARRATOR_")
        # Marketing's worker contract: only the ledger code is a (retryable) 5xx.
        assert status < 500 or code == ErrorCode.LEARN_NARRATOR_LEDGER_ERROR, cls.__name__


def test_every_class_builds_a_worker_error_body_with_its_code_status_and_identifiers():
    for cls in _classes_defined_in_errors():
        exc = cls("complete refused", job_id="job-7", piece_id="piece-3", round=2)
        resp = error_response_from_exception(exc, step="narration_complete")
        body = json.loads(resp.body)
        code, status = EXPECTED[cls.__name__]
        assert resp.status_code == status and body["error_code"] == code, cls.__name__
        assert body["user_message"] == er._USER_MESSAGES[ErrorCode(code)]
        assert "Something went wrong" not in body["user_message"]
        # The ids reach the worker's log through the body (greppable without a repro).
        assert "job_id=job-7" in body["details"]["underlying"] and "round=2" in body["details"]["underlying"]
        assert body["details"]["step"] == "narration_complete"
        assert set(body) == {"error_code", "message", "user_message", "action", "details"}


def test_a_subclass_declared_anywhere_inherits_its_parents_code():
    for sub, parent in _LOCAL_SUBCLASSES.items():
        for message in _BAIT_MESSAGES:
            code, status = classify_exception(sub(message))
            assert (code.value, status) == EXPECTED[parent], (sub.__name__, message)


def test_every_loaded_subclass_of_either_base_classifies_to_a_learn_code():
    """Whatever other module (wave B's writer, ledger, review…) is loaded in this session: every
    subclass of the two bases, however it is named, lands on a LEARN_NARRATOR_* code."""
    subs = _all_subclasses(errors.LearnNarratorError) + _all_subclasses(errors.LearnWeeklyError)
    assert set(_LOCAL_SUBCLASSES) <= set(subs)   # anti-vacuity: the walk sees the local subclasses
    for sub in subs:
        try:
            exc = sub("x")
        except TypeError:
            pytest.fail(f"{sub.__module__}.{sub.__name__} cannot be built from a message — give it the "
                        f"base's (message, **ids) constructor so it can be raised and classified")
        code, _status = classify_exception(exc)
        assert code.value.startswith("LEARN_NARRATOR_"), f"{sub.__module__}.{sub.__name__} → {code.value}"


def test_a_same_named_class_from_another_module_is_never_taken_for_ours():
    for name in EXPECTED:
        impostor = type(name, (Exception,), {"__module__": "somewhere.else"})
        code, _status = classify_exception(impostor("x"))
        assert not code.value.startswith("LEARN_"), name
        assert code == ErrorCode.REPORT_GENERATION_FAILED   # the generic default — never a Learn code
    # …and a class named like ours but under a module whose name merely CONTAINS ours is not ours either.
    shadow = type("LearnNarratorNotFound", (Exception,), {"__module__": "app.services.learn_weekly.errors_old"})
    assert not classify_exception(shadow("x"))[0].value.startswith("LEARN_")


def test_unrelated_exceptions_are_classified_exactly_as_before():
    """The Learn branch runs FIRST; it must claim nothing else."""
    assert classify_exception(ValueError("company profile missing"))[0] == ErrorCode.TICKER_NOT_FOUND
    assert classify_exception(RuntimeError("read timeout"))[0] == ErrorCode.FMP_UNAVAILABLE
    assert classify_exception(RuntimeError("HTTP 429"))[0] == ErrorCode.FMP_RATE_LIMITED
    assert classify_exception(KeyError("x"))[0] == ErrorCode.REPORT_GENERATION_FAILED
    from app.services.marketing import run_service as mrs
    assert classify_exception(mrs.MarketingRunNotHeld("x"))[0] == ErrorCode.MARKETING_RUN_NOT_HELD


def test_the_two_families_stay_apart_and_inherit_nothing_a_heuristic_reads():
    """A broad `except LearnWeeklyError` in the scheduler must not swallow a narrator refusal (or the
    reverse), and neither base may be a ValueError / TimeoutError the heuristics would key on."""
    assert not issubclass(errors.LearnNarratorError, errors.LearnWeeklyError)
    assert not issubclass(errors.LearnWeeklyError, errors.LearnNarratorError)
    for base in (errors.LearnNarratorError, errors.LearnWeeklyError):
        assert base.__bases__ == (Exception,)
    for cls in _classes_defined_in_errors():
        assert issubclass(cls, (errors.LearnNarratorError, errors.LearnWeeklyError)), cls.__name__


def test_the_classifier_table_names_only_classes_errors_py_defines():
    """Renaming a class in errors.py without the table would silently drop it to the heuristics."""
    defined = {c.__name__ for c in _classes_defined_in_errors()}
    assert set(er._LEARN_EXCEPTION_CODES) == defined
    assert er._LEARN_ERRORS_MODULE == errors.__name__


# ── the worker-only codes (iOS never reaches the narrator API) ──────────────────────────────────


def _ios_mapped_codes(codes, swift_source: str) -> List[str]:
    """The codes that appear as a quoted literal in Swift CODE (`//` comments stripped first: the
    comment next to a deliberate non-mapping names the code)."""
    code_only = re.sub(r"//[^\n]*", "", swift_source)
    return [c for c in codes if f'"{c}"' in code_only]


def test_the_ios_scan_is_not_vacuous():
    assert _ios_mapped_codes(["LEARN_NARRATOR_NOT_FOUND"], 'if code == "LEARN_NARRATOR_NOT_FOUND" {') == \
        ["LEARN_NARRATOR_NOT_FOUND"]
    assert _ios_mapped_codes(["LEARN_NARRATOR_NOT_FOUND"], '// "LEARN_NARRATOR_NOT_FOUND" is worker-only') == []


def test_the_learn_codes_are_exactly_the_worker_only_list_and_none_is_mapped_on_ios():
    learn = sorted(c.value for c in ErrorCode if c.value.startswith("LEARN_"))
    assert learn == sorted(WORKER_ONLY_LEARN_CODES)
    for value in WORKER_ONLY_LEARN_CODES:
        code = ErrorCode(value)
        assert code in er._USER_MESSAGES and code in er._DEFAULT_STATUS, value
    assert {EXPECTED[n] for n in EXPECTED} == {(v, er._DEFAULT_STATUS[ErrorCode(v)]) for v in WORKER_ONLY_LEARN_CODES}
    source = _SWIFT_APP_ERROR.read_text(encoding="utf-8")
    assert "mapAPIError" in source   # anti-vacuity: this is the file the mapping lives in
    assert _ios_mapped_codes(WORKER_ONLY_LEARN_CODES, source) == []


# ── the identifiers an exception carries ────────────────────────────────────────────────────────


def test_identifiers_are_rendered_sorted_and_none_is_dropped():
    exc = errors.LearnNarratorJobNotHeld("claim lost", round=3, job_id="j-1", piece_id=None)
    assert str(exc) == "claim lost [job_id=j-1 round=3]"
    assert exc.ids == {"job_id": "j-1", "round": 3}
    assert str(errors.LearnWeeklyError("plain")) == "plain" and errors.LearnWeeklyError("plain").ids == {}
    assert str(errors.LearnNarratorError()) == ""   # buildable with no arguments at all


def test_an_unknown_identifier_is_dropped_with_a_warning_never_rendered_and_never_a_type_error(caplog):
    caplog.set_level(logging.WARNING, logger=errors.__name__)
    secret = "nonce-SENTINEL-9f1c"
    exc = errors.LearnNarratorJobNotHeld("claim lost", job_id="j-1", claim_nonce=secret, signed_url=secret)
    assert isinstance(exc, errors.LearnNarratorJobNotHeld)          # the typed error survives
    assert secret not in str(exc) and secret not in repr(exc) and "claim_nonce" not in exc.ids
    assert str(exc) == "claim lost [job_id=j-1]"
    warned = [r.getMessage() for r in caplog.records if r.name == errors.__name__]
    assert warned and all(secret not in m for m in warned)
    assert "claim_nonce" in warned[0] and "signed_url" in warned[0]


@pytest.mark.parametrize("value, rendered", [
    ("line1\nline2\r\n\tline3", "line1 line2 line3"),      # folded onto one log line
    ("x" * 500, "x" * 80),                                   # capped
    (True, "True"),                                          # a bool is shown as what it is
    (float("nan"), "nan"),
    (-1, "-1"),
    ("", ""),
])
def test_identifier_values_are_folded_and_capped(value, rendered):
    exc = errors.LearnNarratorNotFound("missing", slug=value)
    assert str(exc) == f"missing [slug={rendered}]"
    assert "\n" not in str(exc)


def test_exceptions_survive_pickling_with_their_type_and_message():
    """Sentry and multiprocessing pickle exceptions; the rendered message must come back as is."""
    exc = errors.LearnNarratorAssetMismatch("size differs", job_id="j-9", slug="coreweave")
    back = pickle.loads(pickle.dumps(exc))
    assert type(back) is errors.LearnNarratorAssetMismatch and str(back) == str(exc)
    assert classify_exception(back)[0] == ErrorCode.LEARN_NARRATOR_ASSET_MISMATCH
