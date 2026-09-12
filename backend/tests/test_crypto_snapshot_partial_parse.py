"""A malformed Gemini answer must be REPORTED, and the report must not itself crash.

`_generate_ai_snapshots` had no `else` on `if len(snapshots) == 4`: a response that yielded
1-3 sections was dropped with no log line at all, `finally` re-armed the spawn, and the
template defaults cached in `_build_snapshots` expire after 300 s — so the next view five
minutes later re-spent Gemini, per viewed coin, forever. The only signal was the bill.

The WARNING added for it then crashed on its own arguments: `_parse_ai_snapshots` returns
`List[CryptoSnapshotResponse]`, and `", ".join(sorted(snapshots))` raises TypeError
(`'<' not supported` for 2+ models, `expected str instance` for exactly 1). The enclosing
`except Exception` swallowed that and printed `Crypto snapshot refresh failed … (TypeError)`,
so the one case the branch exists for was the one it could not report (found 2026-09-12,
post-deploy review).
"""

from __future__ import annotations

import logging

import pytest

from app.services.crypto_service import CryptoService

_TWO_SECTIONS = """===CATEGORY===Origin and Technology
Bitcoin was released in 2009.

Proof of work secures the chain.
===CATEGORY===Tokenomics
Twenty-one million cap.

Halving every four years.
"""


def _svc() -> CryptoService:
    return CryptoService.__new__(CryptoService)


@pytest.mark.parametrize("n_sections", [1, 2, 3])
def test_the_partial_parse_warning_does_not_raise(n_sections):
    """The arguments are evaluated eagerly inside the try, so a TypeError here is invisible
    except as a mislabelled 'upstream failed' line."""
    svc = _svc()
    blocks = _TWO_SECTIONS.split("===CATEGORY===")
    body = "".join("===CATEGORY===" + b for b in blocks[1:2] * n_sections)
    snapshots = svc._parse_ai_snapshots(body)
    assert snapshots, "the fixture parsed nothing — it cannot exercise the branch"

    # This is the exact expression the production branch builds.
    rendered = ", ".join(
        sorted(str(getattr(s, "category", "?")) for s in snapshots)
    ) or "none"
    assert isinstance(rendered, str) and rendered != ""


def test_the_models_are_not_directly_sortable_or_joinable():
    """Anti-vacuity: pins WHY the fix is shaped this way. If these ever become strings, the
    test above stops proving anything and this one says so."""
    svc = _svc()
    snapshots = svc._parse_ai_snapshots(_TWO_SECTIONS)
    assert len(snapshots) == 2
    assert not isinstance(snapshots[0], str), "the parser now returns strings — re-derive"
    with pytest.raises(TypeError):
        sorted(snapshots)
    with pytest.raises(TypeError):
        ", ".join(snapshots[:1])


def test_the_branch_renders_the_category_names_not_the_models():
    """Source-level: comment-stripped and function-bound, so the fix cannot be reverted to
    `sorted(snapshots)` while this stays green."""
    import ast
    import inspect
    import re

    from app.services import crypto_service as cs

    src = inspect.getsource(cs)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "_generate_ai_snapshots")
    body = ast.get_source_segment(src, fn)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in body.splitlines())
    assert "sorted(snapshots)" not in code, (
        "the warning sorts Pydantic models again — it raises TypeError and the enclosing "
        "except relabels a parse shortfall as an upstream crash"
    )
    assert 'getattr(s, "category"' in code
    assert "parsed %d/4 sections" in code


@pytest.mark.asyncio
async def test_a_partial_answer_logs_a_warning_and_caches_nothing(monkeypatch, caplog):
    """End to end over the real branch."""
    import app.services.crypto_service as cs

    svc = _svc()
    writes = []

    class _Gem:
        async def generate_text(self, **kw):
            return {"text": _TWO_SECTIONS}          # 2 of 4 sections

    monkeypatch.setattr(cs, "get_gemini_client", lambda: _Gem(), raising=False)
    monkeypatch.setattr(cs, "_cache_set", lambda k, v: writes.append(k), raising=False)
    monkeypatch.setattr(svc, "_save_snapshots_db",
                        lambda *a, **k: writes.append("db"), raising=False)

    with caplog.at_level(logging.WARNING, logger="app.services.crypto_service"):
        await svc._generate_ai_snapshots(
            symbol="BTCUSD", crypto_name="Bitcoin", profile_meta={},
            cache_key="cache:BTCUSD",
        )

    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("parsed 2/4 sections" in m for m in msgs), (
        f"the shortfall was not reported as a shortfall: {msgs}"
    )
    assert not any("TypeError" in m for m in msgs), (
        f"the warning crashed and was relabelled as an upstream failure: {msgs}"
    )
    assert writes == [], "a partial answer must not be cached"
