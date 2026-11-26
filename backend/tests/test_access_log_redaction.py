"""uvicorn's ACCESS log never carries the email link's token_hash (BE-AUTH-01 / DOC-5).

The Confirm-signup email links to `GET /auth/confirm?token_hash=<56 hex>&type=signup`. Until it is
redeemed or expires, that hash is a LIVE single-use credential: GoTrue's public `/verify` turns it
into a session, and `/auth/session-exchange` turns that into Caydex tokens. uvicorn's access log
writes every request's full path WITH its query string, to a logger (`uvicorn.access`) that has its
own handler and `propagate: False` — so the root handlers' `SecretRedactingFilter` never saw it, and
every open of the link (a person's, or a mail scanner's prefetch) put the hash in the Railway log.

The fix (`app.log_redaction.AccessLogRedactingFilter`, attached by `install_uvicorn_log_redaction`
from main.py at import AND at lifespan start) rewrites ONLY `record.args[2]`: uvicorn's
`AccessFormatter` unpacks exactly five args, so the generic filter's `record.args = ()` lost the
line to a ValueError (pinned below, so nobody "simplifies" back to it).

Proof at four levels: a hand-built uvicorn access record through the real `AccessFormatter`; the
real `LOGGING_CONFIG` via `dictConfig`; the real app through TestClient (root + access loggers);
and a REAL uvicorn server on a loopback port serving `app.main.app`, its access stream captured.
"""

from __future__ import annotations

import ast
import io
import logging
import threading
import time
from pathlib import Path

import pytest

import app.main  # noqa: F401 — imported at COLLECTION, before any fixture snapshots logging state
from app.log_redaction import (
    AccessLogRedactingFilter,
    SecretRedactingFilter,
    install_uvicorn_log_redaction,
)

_HASH = "a1" * 28  # 56 hex — the shape of GoTrue's sha224 email token hash
_TARGET = f"/auth/confirm?token_hash={_HASH}&type=signup"
_ACCESS_MSG = '%s - "%s %s HTTP/%s" %d'  # uvicorn's h11_impl / httptools_impl access call
_ACCESS_FMT = '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_MAIN_PY = Path(__file__).resolve().parents[1] / "app" / "main.py"


def _access_record(target=_TARGET, args=None) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, _ACCESS_MSG,
        args if args is not None else ("203.0.113.9:51234", "GET", target, "1.1", 200), None,
    )


def _access_formatter():
    from uvicorn.logging import AccessFormatter

    return AccessFormatter(_ACCESS_FMT, use_colors=False)


@pytest.fixture
def uvicorn_logging_state():
    """Snapshot and restore uvicorn's three loggers: the tests below run `dictConfig` and attach
    filters, which must not leak into the rest of the session."""
    saved = {}
    for name in _UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        saved[name] = (list(lg.handlers), list(lg.filters), lg.propagate, lg.level, lg.disabled)
    try:
        yield
    finally:
        for name, (handlers, filters, propagate, level, disabled) in saved.items():
            lg = logging.getLogger(name)
            lg.handlers[:] = handlers
            lg.filters[:] = filters
            lg.propagate, lg.level, lg.disabled = propagate, level, disabled


# ── the filter, on uvicorn's own record and formatter ─────────────────────────────────────────

def test_a_real_access_record_formats_without_the_hash_and_keeps_its_shape():
    record = _access_record()
    assert AccessLogRedactingFilter().filter(record) is True
    assert isinstance(record.args, tuple) and len(record.args) == 5
    line = _access_formatter().format(record)
    assert _HASH not in line, line
    assert '"GET /auth/confirm?token_hash=***&type=signup HTTP/1.1" 200 OK' in line, line
    assert "203.0.113.9:51234" in line, "the rest of the line is untouched"


def test_the_generic_filter_would_lose_the_line_which_is_why_it_is_never_used_there():
    """THE CAVEAT: `SecretRedactingFilter` sets `record.args = ()` when it changes a message,
    and `AccessFormatter.formatMessage` unpacks five args — the handler raises, the line is lost."""
    record = _access_record()
    SecretRedactingFilter().filter(record)
    assert record.args == ()
    with pytest.raises(ValueError):
        _access_formatter().format(record)


def test_a_clean_access_record_is_left_exactly_as_it_was():
    args = ("127.0.0.1:1", "GET", "/api/v1/stocks/AAPL/overview?range=1y", "1.1", 200)
    record = _access_record(args=args)
    AccessLogRedactingFilter().filter(record)
    assert record.args is args, "no rewrite when nothing secret is in the path"


@pytest.mark.parametrize("target", [
    f"/auth/confirm?token_hash={_HASH}",
    f"/auth/confirm?type=signup&token_hash={_HASH}",
    f"/auth/confirm?token_hash={_HASH}&amp;type=signup",
    f"/auth/confirm?token%5Fhash={_HASH}&type=signup",
    f"/auth/confirm?token_hash={_HASH}&token_hash={_HASH}",
    f"/x?code={_HASH}", f"/x?access_token={_HASH}", f"/x?refresh_token={_HASH}",
    f"/x?apikey={_HASH}", f"/x?key={_HASH}", f"/x?password={_HASH}", f"/x?otp={_HASH}",
])
def test_every_secret_query_shape_is_redacted(target):
    record = _access_record(target=target)
    AccessLogRedactingFilter().filter(record)
    assert _HASH not in _access_formatter().format(record)


@pytest.mark.parametrize("args", [
    None, (), ("only", "four", "args", 1), ("a", "GET", None, "1.1", 200),
    ("a", "GET", 12345, "1.1", 200), {"not": "a tuple"}, ["a", "GET", _TARGET, "1.1", 200],
])
def test_odd_record_shapes_never_raise_and_never_drop_the_record(args):
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "msg", (), None)
    record.args = args  # set after: LogRecord's own constructor rejects some of these shapes
    assert AccessLogRedactingFilter().filter(record) is True


def test_a_path_object_whose_str_raises_never_breaks_the_filter():
    class Hostile:
        def __str__(self):
            raise RuntimeError("no")

    record = _access_record(args=("a", "GET", Hostile(), "1.1", 200))
    assert AccessLogRedactingFilter().filter(record) is True
    assert record.args[2] == "***", "an unreadable path fails CLOSED"
    assert len(record.args) == 5


def test_a_list_shaped_args_is_redacted_and_kept_formattable():
    record = _access_record(args=["203.0.113.9:1", "GET", _TARGET, "1.1", 200])
    AccessLogRedactingFilter().filter(record)
    line = _access_formatter().format(record)
    assert _HASH not in line and "token_hash=***" in line


def test_an_args_free_access_message_is_redacted_in_place():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               f"GET {_TARGET} 200", (), None)
    AccessLogRedactingFilter().filter(record)
    assert _HASH not in record.getMessage()


# ── wiring: install, uvicorn's real LOGGING_CONFIG, main.py ─────────────────────────────────

def _strip_redaction_filters() -> None:
    """Remove what main.py's import-time install attached, so a test proves the install it
    calls itself — not the one that already ran at collection (the fixture restores it after)."""
    for name in _UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        for target in (lg, *lg.handlers):
            for f in list(target.filters):
                if isinstance(f, (AccessLogRedactingFilter, SecretRedactingFilter)):
                    target.removeFilter(f)


def test_install_through_uvicorns_real_logging_config(uvicorn_logging_state, capsys):
    """`dictConfig(LOGGING_CONFIG)` exactly as the uvicorn CLI runs it (Dockerfile CMD and Procfile),
    then the install, then a record emitted through the logger: the stream holds the line without
    the hash, and nothing printed a "--- Logging error ---"."""
    import logging.config

    from uvicorn.config import LOGGING_CONFIG

    _strip_redaction_filters()
    logging.config.dictConfig(LOGGING_CONFIG)
    install_uvicorn_log_redaction()
    install_uvicorn_log_redaction()  # idempotent

    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, AccessLogRedactingFilter) for f in access.filters) == 1
    assert access.handlers, "LOGGING_CONFIG gives uvicorn.access its own handler"
    for handler in access.handlers:
        assert sum(isinstance(f, AccessLogRedactingFilter) for f in handler.filters) == 1
        assert not any(isinstance(f, SecretRedactingFilter) for f in handler.filters), (
            "the args-dropping filter must never sit on an access handler")
    server_handlers = logging.getLogger("uvicorn").handlers
    assert server_handlers and all(
        sum(isinstance(f, SecretRedactingFilter) for f in h.filters) == 1 for h in server_handlers)

    stream = io.StringIO()
    access.handlers[0].setStream(stream)
    access.info(_ACCESS_MSG, "203.0.113.9:51234", "GET", _TARGET, "1.1", 200)
    err_stream = io.StringIO()
    server_handlers[0].setStream(err_stream)
    logging.getLogger("uvicorn.error").error("Exception in ASGI application: GET %s", _TARGET)

    out = stream.getvalue()
    assert "/auth/confirm?token_hash=***&type=signup" in out, out
    assert _HASH not in out and _HASH not in err_stream.getvalue()
    assert "token_hash=***" in err_stream.getvalue()
    assert "Logging error" not in capsys.readouterr().err


def test_the_logger_filter_survives_a_later_dictconfig(uvicorn_logging_state):
    """`uvicorn.run(app_object)` configures logging AFTER the app is imported: `dictConfig`
    replaces the handlers but never removes a logger's filters — the logger-level filter is what
    still covers the access line then (and the lifespan re-install restores the handler ones)."""
    import logging.config

    from uvicorn.config import LOGGING_CONFIG

    _strip_redaction_filters()
    install_uvicorn_log_redaction()
    logging.config.dictConfig(LOGGING_CONFIG)
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, AccessLogRedactingFilter) for f in access.filters)
    stream = io.StringIO()
    access.handlers[0].setStream(stream)
    access.info(_ACCESS_MSG, "c", "GET", _TARGET, "1.1", 200)
    assert _HASH not in stream.getvalue() and "token_hash=***" in stream.getvalue()


def test_install_never_raises(monkeypatch, caplog):
    import app.log_redaction as lr

    def boom(*_a, **_k):
        raise RuntimeError("logging is broken")

    monkeypatch.setattr(lr, "_add_filter_once", boom)
    with caplog.at_level(logging.WARNING, logger="app.log_redaction"):
        lr.install_uvicorn_log_redaction()
    assert any("could not attach the uvicorn log filters" in r.getMessage() for r in caplog.records)


def test_main_installs_it_at_import_and_at_lifespan_start():
    """Brace-bound by AST: a module-level call (the CLI configures uvicorn's logging BEFORE it
    imports the app) and one inside `lifespan` itself, not a mention anywhere else."""
    import app.main  # noqa: F401 — importing is the import-time install

    assert any(isinstance(f, AccessLogRedactingFilter)
               for f in logging.getLogger("uvicorn.access").filters)

    tree = ast.parse(_MAIN_PY.read_text())

    def calls_install(nodes) -> bool:
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "install_uvicorn_log_redaction"
                   for node in nodes for n in ast.walk(node))

    module_level = [n for n in tree.body if isinstance(n, ast.Expr)]
    assert calls_install(module_level), "no module-level install_uvicorn_log_redaction() call"
    lifespan = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
    assert len(lifespan) == 1 and calls_install(lifespan[0].body), "lifespan does not re-install"


# ── end to end: the real app ────────────────────────────────────────────────────────────────

def test_a_get_of_the_link_through_the_app_logs_no_hash(caplog, uvicorn_logging_state):
    """TestClient drives the real app (root loggers: `add_process_time`, the page's own lines)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with caplog.at_level(logging.DEBUG):
        resp = TestClient(app).get(_TARGET)
    assert resp.status_code == 200
    assert _HASH in resp.text, "vacuous: the page did not even receive the hash"
    assert caplog.records, "vacuous: nothing was logged"
    for record in caplog.records:
        assert _HASH not in record.getMessage(), record.name
        assert _HASH not in (record.exc_text or "")


@pytest.mark.parametrize("http", ["h11", "httptools"])
def test_a_real_uvicorn_server_writes_the_access_line_without_the_hash(http, uvicorn_logging_state, capsys):
    """A REAL uvicorn (both HTTP protocols production may pick — `uvicorn[standard]` installs
    httptools; its default LOGGING_CONFIG applied by `Config` AFTER the app was imported, the
    hardest case) serving `app.main.app` on a loopback port. The access stream must hold the request
    line with the hash replaced. Lifespan off (no upstream calls); `loop="asyncio"` because uvloop's
    setup installs a process-wide event-loop policy that would outlive this test."""
    import httpx
    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", access_log=True,
                            log_level="info", http=http, loop="asyncio")
    access = logging.getLogger("uvicorn.access")
    stream = io.StringIO()
    for handler in access.handlers:
        handler.setStream(stream)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline and thread.is_alive():
            time.sleep(0.05)
        assert server.started, "uvicorn did not start"
        port = server.servers[0].sockets[0].getsockname()[1]
        with httpx.Client(trust_env=False, timeout=10) as http_client:  # no env proxy on loopback
            resp = http_client.get(f"http://127.0.0.1:{port}{_TARGET}")
        assert resp.status_code == 200 and _HASH in resp.text
        deadline = time.monotonic() + 5
        while "/auth/confirm" not in stream.getvalue() and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
    out = stream.getvalue()
    assert '"GET /auth/confirm?token_hash=***&type=signup HTTP/1.1" 200' in out, out
    assert _HASH not in out
    captured = capsys.readouterr()
    assert _HASH not in captured.out and _HASH not in captured.err
    assert "Logging error" not in captured.err
