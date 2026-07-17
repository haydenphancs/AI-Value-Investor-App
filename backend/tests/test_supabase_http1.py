"""The Supabase postgrest client must use HTTP/1.1, not HTTP/2.

supabase-py hardcodes http2=True on its sub-clients. Our long-lived singleton's
pooled HTTP/2 connection to the Supabase gateway goes stale (idle-close / GOAWAY
ConnectionTerminated) and REUSE raises httpx.RemoteProtocolError /
LocalProtocolError from the h2 state machine — surfacing as unhandled Sentry
errors (and postgrest APIError 'JSON could not be generated'). A SYNC client gets
no benefit from h2 multiplexing, so we force HTTP/1.1 to remove the reuse race.
"""
from __future__ import annotations

import logging

from postgrest import SyncPostgrestClient

from app.database import _force_http1_on_postgrest


def _http2_enabled(client) -> bool:
    """Best-effort read of an httpx.Client's connection-pool http2 flag."""
    return bool(client._transport._pool._http2)


class _FakeSupabase:
    """Minimal stand-in exposing the `.postgrest` property the swap reaches into."""

    def __init__(self, pg):
        self._pg = pg

    @property
    def postgrest(self):
        return self._pg


def _new_pg():
    return SyncPostgrestClient(
        "https://proj.supabase.co/rest/v1",
        headers={"apikey": "svc-key", "Authorization": "Bearer svc-key"},
    )


def test_swaps_postgrest_session_to_http1_preserving_config():
    pg = _new_pg()
    old = pg.session
    assert _http2_enabled(old) is True  # sanity: supabase-py default is http2

    _force_http1_on_postgrest(_FakeSupabase(pg))

    new = pg.session
    assert new is not old                       # session replaced
    assert _http2_enabled(new) is False         # ...with an HTTP/1.1 client
    assert str(new.base_url).rstrip("/") == "https://proj.supabase.co/rest/v1"
    assert new.headers.get("apikey") == "svc-key"          # auth headers preserved
    assert new.headers.get("Authorization") == "Bearer svc-key"
    assert old.is_closed                        # old h2 session cleaned up
    new.close()


def test_is_best_effort_and_never_breaks_client_creation(caplog):
    class _Broken:
        @property
        def postgrest(self):
            raise RuntimeError("supabase internals changed")

    # Must NOT propagate — a swap failure can't be allowed to break get_supabase().
    with caplog.at_level(logging.WARNING, logger="app.database"):
        _force_http1_on_postgrest(_Broken())
    assert any("keeping the default http2 client" in r.getMessage() for r in caplog.records)
