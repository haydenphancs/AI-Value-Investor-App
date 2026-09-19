"""F24-4: `/health/pdf` renders ONCE per process.

`health_pdf` is `async def` and called `weasyprint.HTML(...).write_pdf()` inline on every
hit — a CPU-bound native render on the single worker's event loop, on an unauthenticated,
unlimited route. `while :; do curl /health/pdf & done` from one host held the loop for a
render per request and every signed-in user's dashboard, chat stream and report poll queued
behind it, with no 429 ever returned. Railway needs exactly one real render per deploy and
pinned versions cannot change in-process, so the first SUCCESS is memoised; a failure never
is, so a broken image keeps failing the deploy gate until it is fixed.

WeasyPrint has no pango locally, so the module is replaced in `sys.modules` — which is also
what makes the render count observable.
"""

from __future__ import annotations

import inspect
import re
import sys
import types

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod


class _FakeWeasy:
    """A `weasyprint` stand-in: `HTML(string=...).write_pdf(buf)` writes a PDF header and
    counts renders; `mode` flips it into a broken renderer."""

    def __init__(self):
        self.renders = 0
        self.mode = "ok"            # "ok" | "raise" | "garbage"
        self.__version__ = "66.0"

    def HTML(self, string=""):
        outer = self

        class _Doc:
            def write_pdf(self, buf):
                outer.renders += 1
                if outer.mode == "raise":
                    raise RuntimeError("transform() takes 1 positional argument")
                buf.write(b"garbage" if outer.mode == "garbage" else b"%PDF-1.7 fake")
        return _Doc()


@pytest.fixture
def weasy(monkeypatch):
    fake = _FakeWeasy()
    monkeypatch.setitem(sys.modules, "weasyprint", fake)
    monkeypatch.setitem(sys.modules, "pydyf", types.SimpleNamespace(__version__="0.11.0"))
    monkeypatch.setattr(main_mod, "_PDF_HEALTH_OK", None)
    return fake


def _client() -> TestClient:
    return TestClient(main_mod.app)   # no context manager: the lifespan must not run


def test_five_hits_render_once(weasy):
    c = _client()
    bodies = [c.get("/health/pdf") for _ in range(5)]
    assert all(r.status_code == 200 for r in bodies)
    assert weasy.renders == 1, f"the renderer ran {weasy.renders}× for 5 unauthenticated hits"
    first = bodies[0].json()
    assert first["status"] == "healthy" and first["weasyprint"] == "66.0" and first["pydyf"] == "0.11.0"
    assert first["rendered_bytes"] == len(b"%PDF-1.7 fake")
    assert all(r.json() == first for r in bodies), "the memoised answer drifted"


def test_a_failure_is_never_memoised(weasy):
    """The deploy gate must keep failing while the image is broken — and recover once it
    renders, without a process restart in between."""
    c = _client()
    weasy.mode = "raise"
    for _ in range(3):
        r = c.get("/health/pdf")
        assert r.status_code == 503 and r.json()["status"] == "degraded"
        assert "RuntimeError" in r.json()["error"]
    assert weasy.renders == 3, "a failure was memoised — the gate could never recover"
    assert main_mod._PDF_HEALTH_OK is None

    weasy.mode = "ok"
    assert c.get("/health/pdf").status_code == 200
    assert c.get("/health/pdf").status_code == 200
    assert weasy.renders == 4, "the first success was not memoised"


def test_a_non_pdf_output_is_a_failure_not_a_memoised_success(weasy):
    c = _client()
    weasy.mode = "garbage"
    r = c.get("/health/pdf")
    assert r.status_code == 503 and "not a PDF" in r.json()["error"]
    assert main_mod._PDF_HEALTH_OK is None
    assert c.get("/health/pdf").status_code == 503 and weasy.renders == 2


def test_the_memo_is_checked_before_the_render_and_only_a_success_is_stored():
    """Source pin, comment-stripped and bound to `health_pdf`: the early return precedes
    the render, and the store sits inside the try's success path."""
    src = inspect.getsource(main_mod.health_pdf)
    code = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
    guard = code.index("if _PDF_HEALTH_OK is not None:")
    render = code.index("write_pdf(")
    store = code.index("_PDF_HEALTH_OK = {")
    fail = code.index("except Exception")
    assert guard < render, "the render runs before the memo is consulted"
    assert render < store < fail, "the memo is stored somewhere other than the success path"
    assert "_PDF_HEALTH_OK" not in code[fail:], "the failure path touches the memo"
