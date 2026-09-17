"""Offline contracts for scripts/pull_testflight_feedback.py.

The script is a thin ASC API drainer; the parts worth pinning are the ones that decide what
lands on disk and how it is named — a wrong folder stamp or a dropped item is invisible at
run time because the summary line only counts. No network: every helper is exercised on
synthetic payloads shaped like the documented API objects.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pull_testflight_feedback.py"


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("pull_testflight_feedback", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(m)
    # Never let the test read the developer's real backend/.env.
    monkeypatch.setattr(m, "_BACKEND_ENV", tmp_path / "absent.env")
    for name in m._KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    return m


# ── naming / filtering ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, stamp",
    [
        ("2026-09-15T18:22:07.123-07:00", "20260915T182207"),
        ("2026-09-15T18:22:07Z", "20260915T182207"),
        ("2026-09-15T18:22:07+00:00", "20260915T182207"),
        ("", "unknown"),
    ],
)
def test_folder_name_is_sortable_and_filesystem_safe(mod, raw, stamp):
    assert mod._folder_name(raw, "abc") == f"{stamp}_abc"


def test_since_filter_keeps_unparsable_dates_rather_than_dropping_items(mod):
    since = datetime(2026, 9, 10, tzinfo=timezone.utc)
    assert mod._since_ok("2026-09-15T00:00:00Z", since)
    assert not mod._since_ok("2026-09-01T00:00:00Z", since)
    assert mod._since_ok("2026-09-15T00:00:00", since)  # naive → UTC
    assert mod._since_ok("garbage", since)  # a bad stamp must never silently drop feedback
    assert mod._since_ok(None, since)
    assert mod._since_ok("2026-01-01T00:00:00Z", None)


# ── meta shaping ────────────────────────────────────────────────────────────

def _included(mod):
    return mod._index_included(
        [
            {"type": "builds", "id": "b1",
             "attributes": {"version": "42", "preReleaseVersion": "1.0.3"}},
            {"type": "betaTesters", "id": "t1",
             "attributes": {"firstName": "A", "lastName": "B", "email": "a@b.c"}},
        ]
    )


def test_shape_meta_joins_build_and_tester_from_included(mod):
    item = {
        "id": "s1",
        "attributes": {"createdDate": "2026-09-15T18:22:07Z", "comment": "hi", "email": "attr@x.y"},
        "relationships": {
            "build": {"data": {"type": "builds", "id": "b1"}},
            "tester": {"data": {"type": "betaTesters", "id": "t1"}},
        },
    }
    meta = mod._shape_meta("screenshot", item, _included(mod))
    assert meta["kind"] == "screenshot" and meta["comment"] == "hi"
    assert meta["build"] == {"id": "b1", "buildNumber": "42", "appVersion": "1.0.3",
                             "uploadedDate": None, "expired": None}
    assert meta["tester"]["email"] == "a@b.c" and meta["tester"]["firstName"] == "A"


def test_shape_meta_degrades_on_missing_or_dangling_relationships(mod):
    bare = {"id": "s2", "attributes": {"createdDate": "2026-09-15T18:22:07Z", "email": "attr@x.y"}}
    meta = mod._shape_meta("screenshot", bare, _included(mod))
    assert meta["build"]["id"] is None
    assert meta["tester"]["email"] == "attr@x.y"  # falls back to the attribute

    dangling = {
        "id": "s3",
        "attributes": {},
        "relationships": {"build": {"data": {"type": "builds", "id": "nope"}}, "tester": {"data": None}},
    }
    meta = mod._shape_meta("crash", dangling, _included(mod))
    assert meta["build"]["buildNumber"] is None and meta["tester"]["id"] is None


# ── index ───────────────────────────────────────────────────────────────────

def test_rebuild_index_walks_both_kinds_newest_first_and_skips_unreadable(mod, tmp_path, capsys):
    (tmp_path / "feedback" / "20260915T182207_s1").mkdir(parents=True)
    (tmp_path / "feedback" / "20260915T182207_s1" / "meta.json").write_text(json.dumps(
        {"id": "s1", "kind": "screenshot", "createdDate": "2026-09-15T18:22:07Z",
         "build": {"appVersion": "1.0.3", "buildNumber": "42"}, "files": ["screenshot_1.png"]}))
    (tmp_path / "crashes" / "20260916T000000_c1").mkdir(parents=True)
    (tmp_path / "crashes" / "20260916T000000_c1" / "meta.json").write_text(json.dumps(
        {"id": "c1", "kind": "crash", "createdDate": "2026-09-16T00:00:00Z", "files": ["crash.txt"]}))
    (tmp_path / "feedback" / "bad_x").mkdir(parents=True)
    (tmp_path / "feedback" / "bad_x" / "meta.json").write_text("{not json")

    rows = mod._rebuild_index(tmp_path)

    assert [r["id"] for r in rows] == ["c1", "s1"]
    assert rows[0]["dir"] == "crashes/20260916T000000_c1"
    assert rows[1]["files"] == ["screenshot_1.png"] and rows[1]["appVersion"] == "1.0.3"
    assert json.loads((tmp_path / "index.json").read_text()) == rows
    assert "unreadable" in capsys.readouterr().out  # loud, not silent


# ── download ────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, ctype, body=b"x"):
        self.status_code = status
        self.headers = {"content-type": ctype}
        self.content = body


class _Client:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def get(self, url, follow_redirects=True, **kw):
        self.calls.append(kw)
        return self.resp


@pytest.mark.parametrize(
    "ctype, url, ext",
    [
        ("image/png", "https://x/y", ".png"),
        ("image/jpeg; charset=binary", "https://x/y", ".jpg"),
        ("application/octet-stream", "https://x/y/shot.JPEG?sig=1", ".jpg"),
        ("application/octet-stream", "https://x/y/blob?sig=1", ".bin"),
    ],
)
def test_download_picks_extension_from_content_type_then_url(mod, tmp_path, ctype, url, ext):
    client = _Client(_Resp(200, ctype))
    saved = mod._download(client, url, tmp_path / "screenshot_1")
    assert saved is not None and saved.name == f"screenshot_1{ext}" and saved.read_bytes() == b"x"
    # A signed third-party URL must not receive the ASC bearer token.
    assert all("headers" not in c for c in client.calls)


def test_download_reports_an_expired_url_instead_of_writing_garbage(mod, tmp_path, capsys):
    assert mod._download(_Client(_Resp(403, "text/plain")), "https://x/y", tmp_path / "s") is None
    assert not list(tmp_path.iterdir())
    assert "expired" in capsys.readouterr().out


# ── credentials ─────────────────────────────────────────────────────────────

def test_credentials_env_wins_and_env_file_fills_only_the_gaps(mod, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ASC_KEY_ID=FILEKEY\nASC_ISSUER_ID=file-issuer\n"
                        "ASC_PRIVATE_KEY_PATH=/file/path\nOTHER=1\n")
    monkeypatch.setattr(mod, "_BACKEND_ENV", env_file)
    monkeypatch.setenv("ASC_KEY_ID", "ENVKEY")
    assert mod._credentials() == {
        "ASC_KEY_ID": "ENVKEY", "ASC_ISSUER_ID": "file-issuer", "ASC_PRIVATE_KEY_PATH": "/file/path",
    }


def test_credentials_names_the_missing_vars_and_never_echoes_a_value(mod, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ASC_KEY_ID=FILEKEY\n")
    monkeypatch.setattr(mod, "_BACKEND_ENV", env_file)
    with pytest.raises(SystemExit) as exc:
        mod._credentials()
    msg = str(exc.value)
    assert "ASC_ISSUER_ID, ASC_PRIVATE_KEY_PATH" in msg
    assert "FILEKEY" not in msg

    monkeypatch.setattr(mod, "_BACKEND_ENV", tmp_path / "absent.env")
    with pytest.raises(SystemExit) as exc:
        mod._credentials()
    assert "ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY_PATH" in str(exc.value)


def test_token_rejects_a_key_file_that_does_not_match_the_key_id(mod, tmp_path, monkeypatch):
    other = tmp_path / "AuthKey_OTHERKEY1.p8"
    other.write_text("not a key")
    monkeypatch.setenv("ASC_KEY_ID", "RIGHTKEY12")
    monkeypatch.setenv("ASC_ISSUER_ID", "12345678-1234-1234-1234-123456789012")
    monkeypatch.setenv("ASC_PRIVATE_KEY_PATH", str(other))
    with pytest.raises(SystemExit) as exc:
        mod._token()
    assert "must be the same key" in str(exc.value)


def test_the_script_never_issues_a_mutating_request(mod):
    # The API offers DELETE on both feedback resources; the drainer is read-only by contract.
    src = _SCRIPT.read_text()
    assert ".delete(" not in src and ".post(" not in src and ".patch(" not in src
    assert 'client.request(' not in src


# ── build → app version back-fill ───────────────────────────────────────────

def test_backfill_resolves_each_build_once_and_only_touches_unlabelled_metas(mod, tmp_path, monkeypatch):
    def write(sub, name, build):
        d = tmp_path / sub / name
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"id": name, "kind": "screenshot", "build": build}))
        return d / "meta.json"

    a = write("feedback", "a", {"id": "b1", "appVersion": None})
    b = write("feedback", "b", {"id": "b1", "appVersion": None})      # same build → one call
    c = write("crashes", "c", {"id": "b2", "appVersion": "1.0"})      # already labelled → untouched
    d = write("feedback", "d", {"id": None, "appVersion": None})      # no build → untouched
    e = write("feedback", "e", {"id": "b3", "appVersion": None})      # resolves to nothing → warned

    calls = []

    def fake_get(client, token, path, **params):
        calls.append(path)
        return {"data": {"attributes": {"version": "1.0"}}} if "b1" in path else {"data": {}}

    monkeypatch.setattr(mod, "_get", fake_get)
    fixed = mod._backfill_app_versions(object(), "tok", tmp_path)

    assert fixed == 2
    assert calls == ["/v1/builds/b1/preReleaseVersion", "/v1/builds/b3/preReleaseVersion"]
    assert json.loads(a.read_text())["build"]["appVersion"] == "1.0"
    assert json.loads(b.read_text())["build"]["appVersion"] == "1.0"
    assert json.loads(c.read_text())["build"]["appVersion"] == "1.0"
    assert json.loads(d.read_text())["build"]["appVersion"] is None
    assert json.loads(e.read_text())["build"]["appVersion"] is None
