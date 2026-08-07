"""Two defects that both hid behind something that *looked* like it worked.

1. **Account deletion abandoned PDFs past the 100th object.** storage3 caps an unpaginated
   `list()` at 100 (`DEFAULT_SEARCH_OPTIONS = {"limit": 100, ...}`). One call deleted the
   first 100, returned success, and left the rest in Storage forever while the endpoint
   reported a complete deletion — against a privacy policy that promises otherwise. A Max
   subscriber gets 4000 credits ÷ 20 = 200 reports/month, so the heaviest users were exactly
   the ones whose data survived. It passed its tests because the fake returned everything in
   one page and never actually removed anything.

2. **A whitespace-padded bearer skipped password-change token eviction.**
   `get_current_user_id` strips before verifying, so `Bearer  <token>` (two spaces —
   FastAPI splits on the FIRST space only, so the credential keeps a leading one)
   authenticates fine. But `get_current_user` passed the RAW value to
   `_reject_if_password_changed_since_issue`, which failed to decode it, hit its fail-open
   `except`, and returned the user. So a stolen token survived the victim's password reset.
"""
from __future__ import annotations

import inspect

import pytest

from app.api.v1.endpoints import users as users_ep
from app import dependencies as deps


# ── 1. Deletion pagination ────────────────────────────────────────────────────

class _Bucket:
    """Faithful storage3 stand-in: page-capped list, and remove() really removes."""

    def __init__(self, names):
        self.names = list(names)
        self.list_calls = 0

    def list(self, prefix, options=None):
        self.list_calls += 1
        limit = (options or {}).get("limit", 100)
        offset = (options or {}).get("offset", 0)
        return [{"name": n} for n in sorted(self.names)[offset: offset + limit]]

    def remove(self, paths):
        for p in paths:
            n = p.rsplit("/", 1)[-1]
            if n in self.names:
                self.names.remove(n)


class _SB:
    def __init__(self, bucket):
        self._b = bucket
        self.storage = self

    def from_(self, _name):
        return self._b


@pytest.mark.parametrize("count", [0, 1, 99, 100, 101, 250])
def test_every_pdf_is_deleted_regardless_of_count(count):
    """101 is the case that shipped broken: exactly one object survived, silently."""
    bucket = _Bucket([f"r{i}.pdf" for i in range(count)])
    err = users_ep._purge_research_pdfs(_SB(bucket), "u1")
    assert err is None, f"reported failure: {err}"
    assert bucket.names == [], f"{len(bucket.names)} PDF(s) survived deletion"


def test_more_than_one_page_really_does_paginate():
    """Guards against a fix that just raises the limit to 1000 — that only moves the cliff."""
    bucket = _Bucket([f"r{i}.pdf" for i in range(250)])
    users_ep._purge_research_pdfs(_SB(bucket), "u1")
    assert bucket.list_calls >= 3, "expected repeated listing, not one big page"


def test_a_bucket_that_never_empties_reports_failure_instead_of_looping_forever():
    """If remove() silently no-ops (permissions, eventual consistency), the loop must
    terminate and report — not spin, and not claim success."""
    class _Stuck(_Bucket):
        def remove(self, paths):
            pass

    bucket = _Stuck([f"r{i}.pdf" for i in range(150)])
    err = users_ep._purge_research_pdfs(_SB(bucket), "u1")
    assert err is not None, "a never-emptying bucket must not report success"
    assert "pages" in err


def test_a_list_failure_is_still_reported():
    class _Broken(_Bucket):
        def list(self, prefix, options=None):
            raise RuntimeError("storage down")

    err = users_ep._purge_research_pdfs(_SB(_Broken([])), "u1")
    assert err is not None and "RuntimeError" in err


# ── 2. Bearer normalisation ───────────────────────────────────────────────────

def test_password_change_check_receives_a_stripped_token():
    """Source-scan: the two call sites must normalise identically. `get_current_user_id`
    strips before verifying; if this one does not, a padded bearer authenticates AND skips
    eviction."""
    src = inspect.getsource(deps)
    call = "_reject_if_password_changed_since_issue("
    assert call in src
    for line in src.splitlines():
        if call in line and "def " not in line:
            assert ".strip()" in line, (
                "the password-change eviction check is being handed an unstripped "
                "credential — a padded bearer will bypass it"
            )
            break
    else:
        pytest.fail("no call site found")


def test_a_padded_bearer_decodes_to_the_same_token_after_stripping():
    """The mechanism, demonstrated: FastAPI splits the header on the FIRST space only, so a
    double space leaves a leading space on the credential. Stripped, it is the same token;
    unstripped, it fails to decode — which is what triggered the fail-open."""
    from fastapi.security.utils import get_authorization_scheme_param

    scheme, credential = get_authorization_scheme_param("Bearer  abc.def.ghi")
    assert scheme == "Bearer"
    assert credential == " abc.def.ghi"          # the leading space that caused this
    assert credential.strip() == "abc.def.ghi"
