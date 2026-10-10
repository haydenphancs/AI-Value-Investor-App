"""
`marketing/main.py` — the media worker: the ET gate, the manifest, and the whole
claim → register → signed PUT → complete → checkpoint conversation against a fake backend.

The worker package `backend/marketing/` is STANDALONE (imports nothing from app.*); this
test loads the entrypoint by path (a fresh module per test, so import-time side effects such
as logging setup are exercised) and scans EVERY file in the package for that property,
because importing app.config in that container would fail (no SUPABASE_URL) and importing
app.main would double-start every lifespan loop.
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx
import pytest

_PKG = Path(__file__).resolve().parents[1] / "marketing"
_SCRIPT = _PKG / "main.py"
ET = ZoneInfo("America/New_York")


def _load():
    spec = importlib.util.spec_from_file_location("marketing_daily_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture
def m():
    return _load()


# ── standalone-ness ───────────────────────────────────────────────────────────


#: gitignored, never deployed (Railway builds from git) — and a model/pip cache may hold
#: third-party .py files that are not ours to police. Mirrors .gitignore EXACTLY: `out/`,
#: `models/` and `.cache/` are anchored at the package root (`backend/marketing/models/`), while
#: `__pycache__` and `.cache` are ignored at every depth. A NESTED `voice/models/` is tracked and
#: ships in the image, so it must be scanned — skipping the name at any depth hid it.
_ROOT_UNTRACKED = {"out", "models", ".cache"}
_ANY_DEPTH_UNTRACKED = {"__pycache__", ".cache"}
#: Calls that import by NAME: a literal target is checked like an import statement, anything
#: else fails closed (it could not be checked).
_NAME_IMPORTERS = {"import_module", "__import__", "run_module", "resolve_name"}
#: Calls that load code by PATH or from source text: the worker has no reason to, and a path
#: can point straight at app/config.py — always a problem.
_CODE_LOADERS = {"run_path", "spec_from_file_location", "spec_from_loader", "SourceFileLoader",
                 "SourcelessFileLoader", "module_from_spec", "exec_module", "load_module"}
_EVAL_BUILTINS = {"exec", "eval", "compile"}  # bare-name only: `re.compile` is fine
#: A string that IS a dotted app module name (`"app.config"`) — belt-and-braces for a name
#: handed to an importer indirectly. Bare `"app"` is not flagged: `Path("/") / "app"` is a path.
_APP_DOTTED = re.compile(r"app(?:\.[A-Za-z_]\w*)+")


def _untracked_dir(rel_dirs: tuple) -> bool:
    """`rel_dirs`: the directory parts of a path relative to the package root."""
    return bool(rel_dirs) and (rel_dirs[0] in _ROOT_UNTRACKED
                               or bool(_ANY_DEPTH_UNTRACKED & set(rel_dirs)))


def _worker_files(root: Path = _PKG) -> List[Path]:
    return sorted(p for p in root.rglob("*.py")
                  if not _untracked_dir(p.relative_to(root).parts[:-1]))


def _copy_ignore(root: Path):
    """`shutil.copytree` ignore callable with the same anchored rule (plus the root `assets/`,
    which holds fonts, no code) — the copy must be what `COPY . marketing/` ships."""
    def ignore(src: str, names: List[str]) -> set:
        rel = Path(src).relative_to(root).parts
        return {n for n in names if _untracked_dir(rel + (n,)) or (not rel and n == "assets")}
    return ignore


def _worker_module_of(path: Path, root: Path = _PKG):
    parts = ["marketing", *path.relative_to(root).with_suffix("").parts]
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts.pop()
    return ".".join(parts), is_pkg


def _terminal_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _literal(node: ast.Call, pos: int, kw: str):
    if len(node.args) > pos:
        a = node.args[pos]
        return a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
    for k in node.keywords:
        if k.arg == kw:
            return k.value.value if isinstance(k.value, ast.Constant) and isinstance(k.value.value, str) else None
    return None


def worker_imports(src: str, module: str, is_pkg: bool = False):
    """(imported dotted names, problems) for one worker source file — an AST scan, so a
    comment or docstring can neither satisfy nor trip it, and an import nested in a function
    (the package's lazy-import idiom) is seen like a top-level one. Self-contained on purpose:
    the FMP boundary scanner in test_marketing_import_boundary.py enforces a different policy,
    and a change there must not silently change this guard.

    It replaced a line regex (`^\\s*(from|import)\\s+app[.\\s]`) that `importlib.import_module`,
    `__import__`, `import os, app.x` and `import json; import app.x` all walked past.
    Mutation-tested by hand (2026-09-23) on a scratch copy of main.py: each of those forms, the
    same inside a stage function, a non-literal `import_module(n)` and a plain static import
    turned the real-tree test red; the module-level ones also turn the fresh-interpreter test
    below red."""
    tree = ast.parse(src)
    pkg = module.split(".") if is_pkg else module.split(".")[:-1]
    imported: List[tuple] = []
    problems: List[tuple] = []
    aliases: Dict[str, str] = {}  # `from importlib import import_module as im` → im
    call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}

    def resolve(level: int, target: str, base_pkg: List[str], line: int):
        if level - 1 >= len(base_pkg):
            problems.append((line, f"relative import (level {level}) beyond the top-level package"))
            return None
        base = base_pkg[: len(base_pkg) - (level - 1)]
        return ".".join(base + ([target] if target else []))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = resolve(node.level, node.module or "", pkg, node.lineno) if node.level else node.module
            if base is None:
                continue
            imported.append((node.lineno, base))
            for a in node.names:
                if a.name != "*":
                    imported.append((node.lineno, f"{base}.{a.name}"))
                if a.name in _NAME_IMPORTERS | _CODE_LOADERS:
                    aliases[a.asname or a.name] = a.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _APP_DOTTED.fullmatch(node.value):
                problems.append((node.lineno, f"string {node.value!r} names an app module"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            raw = _terminal_name(node.func)
            name = aliases.get(raw, raw)
            if isinstance(node.func, ast.Name) and raw in _EVAL_BUILTINS:
                problems.append((node.lineno, f"{raw}() runs source text"))
            elif name in _CODE_LOADERS:
                problems.append((node.lineno, f"{name}() loads code by path"))
            elif name in _NAME_IMPORTERS:
                target = _literal(node, 0, "name" if name != "run_module" else "mod_name")
                if target is None:
                    problems.append((node.lineno, f"dynamic {name}() with a non-literal target"))
                    continue
                if target.startswith("."):
                    level = len(target) - len(target.lstrip("."))
                    package = (_literal(node, 1, "package") or "").split(".")
                    target = resolve(level, target.lstrip("."), [p for p in package if p], node.lineno)
                    if target is None:
                        continue
                imported.append((node.lineno, target))
            elif raw == "getattr" and (_literal(node, 1, "name") or "") in _NAME_IMPORTERS | _CODE_LOADERS:
                problems.append((node.lineno, "an importer fetched by getattr"))
        elif isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in call_funcs:
            ref = _terminal_name(node)
            if (ref in _NAME_IMPORTERS | _CODE_LOADERS or ref in aliases
                    or (isinstance(node, ast.Name) and ref in _EVAL_BUILTINS)):
                problems.append((node.lineno, f"{ref} referenced without a direct, checkable call"))
    return imported, problems


def _app_violations(imported, problems) -> List[str]:
    bad = [f"{line}: imports {n}" for line, n in imported if n == "app" or n.startswith("app.")]
    return bad + [f"{line}: {what}" for line, what in problems]


def test_worker_package_imports_nothing_from_app():
    files = _worker_files()
    assert _SCRIPT in files and len(files) >= 2, files
    seen_httpx = False
    for py in files:
        module, is_pkg = _worker_module_of(py)
        imported, problems = worker_imports(py.read_text(encoding="utf-8"), module, is_pkg)
        assert _app_violations(imported, problems) == [], (
            f"{py.relative_to(_PKG)}: the worker must stay importable without app.config "
            "(no SUPABASE_* in its container)"
        )
        seen_httpx |= py == _SCRIPT and "httpx" in {n for _, n in imported}
    assert seen_httpx  # anti-vacuity: the scan really parsed main.py's imports


@pytest.mark.parametrize("src", [
    "from app.schemas.marketing import RUN_STAGES",
    "import app.config",
    "import app",
    "import app.config as cfg",
    "import os, app.schemas.marketing",
    "import json; import app.schemas.marketing",
    "def stage():\n    from app.config import Settings\n",
    "import importlib\n_cfg = importlib.import_module('app.schemas.marketing')",
    "__import__('app.schemas.marketing')",
    "def _h():\n    import importlib\n    return importlib.import_module('app.config')",
    "from importlib import import_module as im\nim('app.config')",
    "from importlib import import_module\nimport_module(name='app.main')",
    "import importlib\nimportlib.import_module(some_name)",
    "import importlib\nimportlib.import_module('.config', package='app')",
    "import importlib\nf = importlib.import_module\nf('x')",
    "import importlib\ngetattr(importlib, 'import_module')('x')",
    "exec('import app.config')",
    "eval(\"__import__('ap' + 'p')\")",
    "run = exec\nrun('x = 1')",
    "import runpy\nrunpy.run_module('app.main')",
    "import runpy\nrunpy.run_path('/srv/backend/app/main.py')",
    "import importlib.util\nimportlib.util.spec_from_file_location('c', '/srv/app/config.py')",
    "from .. import app",
    "target = 'app.config'",
])
def test_the_worker_scanner_flags(src):
    imported, problems = worker_imports(src, "marketing.main")
    assert _app_violations(imported, problems), src


@pytest.mark.parametrize("src", [
    "import httpx\nimport json",
    "from . import helpers\nfrom .stages import voice",
    '"""Never import app.config here; app.main would double-start the lifespan."""\n',
    "# from app.config import Settings\nx = 1",
    "import re\nP = re.compile(r'x')",
    "FONTS = '/app/marketing/assets/fonts'",
    "import importlib\nimportlib.import_module('marketing.helpers')",
    "import apple\nimport application_helpers",
    "import subprocess\nsubprocess.run(['ffmpeg', '-version'])",
    "from pathlib import Path\nROOT = Path('/') / 'app'",
])
def test_the_worker_scanner_passes(src):
    imported, problems = worker_imports(src, "marketing.main")
    assert _app_violations(imported, problems) == [], (src, imported, problems)


@pytest.mark.parametrize("src, name", [
    ("import importlib\nimportlib.import_module(NAME)".replace("NAME", repr("app.config")), "app.config"),
    ("__import__(NAME)".replace("NAME", repr("app.main")), "app.main"),
    ("from importlib import import_module as im\nim(name=NAME)".replace("NAME", repr("app.x")), "app.x"),
    ("import importlib\nimportlib.import_module('.config', package='app')", "app.config"),
    ("import runpy\nrunpy.run_module(NAME)".replace("NAME", repr("app.main")), "app.main"),
])
def test_a_literal_dynamic_import_is_resolved_like_an_import_statement(src, name):
    """Each form on its own branch — not only caught by the string belt."""
    imported, _ = worker_imports(src, "marketing.main")
    assert name in {n for _, n in imported}


def test_the_worker_scanner_resolves_relative_imports_inside_the_package():
    imported, problems = worker_imports("from . import a\nfrom .b import c", "marketing.main")
    assert {"marketing.a", "marketing.b", "marketing.b.c"} <= {n for _, n in imported} and not problems
    imported, _ = worker_imports("from . import a", "marketing", is_pkg=True)
    assert "marketing.a" in {n for _, n in imported}


def test_the_worker_package_imports_in_a_tree_without_app(tmp_path):
    """The Docker layout: only `marketing/` exists. Importing the entrypoint in a fresh
    interpreter there must work and load no `app` module, by any route — including one the
    AST scan cannot see (a helper that imports app at module level)."""
    shutil.copytree(_PKG, tmp_path / "marketing", ignore=_copy_ignore(_PKG))
    code = (
        "import importlib.util, json, sys\n"
        "assert importlib.util.find_spec('app') is None, 'app is importable: the check is vacuous'\n"
        "import marketing.main\n"
        # EVERY module of the package, not just the entrypoint: main imports the voice stage
        # lazily, and `python -m marketing.voice child` is the image's second entrypoint.
        "import pkgutil, importlib, marketing\n"
        "mods = [m.name for m in pkgutil.iter_modules(marketing.__path__)]\n"
        "assert {'main', 'voice', 'timings', 'captions', 'preview'} <= set(mods), mods\n"
        "[importlib.import_module('marketing.' + m) for m in mods]\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "MARKETING_LOG_LEVEL": "WARNING"})
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True,
                          text=True, timeout=120, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert {"marketing.main", "marketing.voice", "marketing.timings", "marketing.captions",
            "marketing.preview"} <= set(loaded)
    assert [n for n in loaded if n == "app" or n.startswith("app.")] == []


def test_the_untracked_rule_is_anchored_like_the_gitignore(tmp_path):
    """A nested `models/` or `out/` package is tracked and deployed, so both guards must see it;
    only the ROOT ones (and `__pycache__` / `.cache` anywhere) are skipped."""
    root = tmp_path / "marketing"
    files = {
        "voice/models/kokoro_loader.py": True,   # nested: tracked, shipped, scanned
        "render/out/helpers.py": True,
        "models/hf_cache_module.py": False,      # root: gitignored model cache
        "out/scratch.py": False,
        ".cache/pip/x.py": False,
        "voice/.cache/y.py": False,               # `.cache` is ignored at every depth
        "voice/__pycache__/z.py": False,
        "main.py": True,
    }
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x = 1\n")
    scanned = {p.relative_to(root).as_posix() for p in _worker_files(root)}
    assert scanned == {rel for rel, keep in files.items() if keep}
    copy = tmp_path / "copy"
    shutil.copytree(root, copy, ignore=_copy_ignore(root))
    copied = {p.relative_to(copy).as_posix() for p in copy.rglob("*.py")}
    assert copied == scanned


def test_a_nested_models_package_importing_app_is_flagged(tmp_path):
    root = tmp_path / "marketing"
    (root / "voice" / "models").mkdir(parents=True)
    (root / "voice" / "models" / "loader.py").write_text(
        "def load():\n    from app.config import settings\n    return settings\n")
    problems = []
    for path in _worker_files(root):
        module, is_pkg = _worker_module_of(path, root)
        imported, found = worker_imports(path.read_text(), module, is_pkg)
        problems += _app_violations(imported, found)
    assert problems, "a nested models/ package importing app.* went unseen"


def test_worker_package_is_self_contained_for_docker():
    """The Dockerfile COPYs only `marketing/`; the run command and the fonts path must agree."""
    docker = (_PKG / "Dockerfile").read_text()
    # The build context is the package itself (Root Directory /backend/marketing), so every COPY
    # lands under /app/marketing and nothing outside the package can be copied in.
    assert 'COPY . marketing/' in docker and '"-m", "marketing.main"' in docker
    copies = [l for l in docker.splitlines() if l.startswith("COPY ")]
    assert copies and all(l.split()[-1] == "marketing/" for l in copies), copies
    assert not any(".." in part for l in copies for part in l.split()[1:]), copies
    ignore = (_PKG / ".dockerignore").read_text().split()
    assert {"out/", "models/", ".cache/"} <= set(ignore), ignore
    toml = (_PKG / "railway.toml").read_text()
    assert 'dockerfilePath = "Dockerfile"' in toml and "RAILWAY DOES NOT READ THIS FILE" in toml
    assert 'startCommand = "python -m marketing.main"' in toml


def _pins(text: str) -> Dict[str, str]:
    """`name==version` lines (comments and blanks skipped), names normalised like pip does."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "@" in line:          # the hash-pinned spaCy model URL
            continue
        name, sep, version = line.partition("==")
        assert sep and version and not any(c in version for c in "<>=!~*, "), f"not an exact pin: {raw!r}"
        name = re.sub(r"[-_.]+", "-", name.split("[", 1)[0]).lower()
        out[name] = version
    return out


def test_every_worker_package_is_pinned_and_the_dockerfile_applies_the_pins():
    """The first Railway build is the image's first build anywhere: every transitive package must
    resolve to the version the 2026-09-26 spike measured, not whatever PyPI serves that day.
    kokoro/misaki/spaCy leave transformers, huggingface_hub, thinc, … unpinned themselves."""
    reqs = _pins((_PKG / "requirements.txt").read_text())
    cons = _pins((_PKG / "constraints.txt").read_text())
    # The packages whose drift would change what the image does, named so a regenerated file
    # cannot quietly lose them.
    for name in ("transformers", "huggingface-hub", "spacy", "thinc", "loguru", "curated-transformers",
                 "phonemizer-fork", "espeakng-loader", "tokenizers", "safetensors", "numpy", "torch",
                 "kokoro", "misaki", "pillow", "fonttools", "httpx"):
        assert name in cons, f"{name} is not pinned in marketing/constraints.txt"
    for name, version in reqs.items():
        assert cons.get(name) == version, f"requirements.txt pins {name}=={version}, constraints {cons.get(name)}"
    docker = (_PKG / "Dockerfile").read_text()
    install = next(l for l in docker.splitlines() if "-r marketing/requirements.txt" in l)
    assert "-c marketing/constraints.txt" in install, install
    assert re.search(r"^COPY requirements\.txt constraints\.txt marketing/$", docker, re.M)
    # torch comes from the CPU index at the SAME version the constraints pin.
    assert f"torch=={cons['torch']}" in docker


def test_run_stages_mirror_the_schema(m):
    from app.schemas.marketing import RUN_STAGES

    assert m.RUN_STAGES == RUN_STAGES


# ── pure helpers ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hour, run_hour, expected",
    [(15, 16, False), (16, 16, True), (23, 16, True), (0, 16, False), (0, 0, True), (7, 8, False)],
)
def test_should_run_now_gates_on_the_et_hour(m, hour, run_hour, expected):
    now = datetime(2026, 9, 17, hour, 30, tzinfo=ET)
    assert m.should_run_now(now, run_hour) is expected


def test_should_run_now_force_bypasses_and_bad_hour_raises(m):
    assert m.should_run_now(datetime(2026, 9, 17, 1, 0, tzinfo=ET), 16, force=True) is True
    with pytest.raises(ValueError):
        m.should_run_now(datetime(2026, 9, 17, 1, 0, tzinfo=ET), 24)


@pytest.mark.parametrize("raw, default, expected", [
    (None, True, True), ("", False, False), ("1", False, True), ("true", False, True),
    ("YES", False, True), ("0", True, False), ("false", True, False), ("nah", True, False),
])
def test_env_flag(m, monkeypatch, raw, default, expected):
    if raw is None:
        monkeypatch.delenv("MK_TEST_FLAG", raising=False)
    else:
        monkeypatch.setenv("MK_TEST_FLAG", raw)
    assert m.env_flag("MK_TEST_FLAG", default) is expected


def test_build_manifest_lists_fonts_and_tolerates_a_missing_dir(m, tmp_path):
    (tmp_path / "Inter-Bold.ttf").write_bytes(b"x")
    (tmp_path / "notes.md").write_bytes(b"x")
    (tmp_path / "Inter-Regular.TTF").write_bytes(b"x")
    man = m.build_manifest(worker_version="v", run_date=datetime(2026, 9, 17).date(), dry_run=True,
                           ffmpeg="ffmpeg version 5.1.9", fonts_dir=str(tmp_path))
    assert man["fonts"] == ["Inter-Bold.ttf", "Inter-Regular.TTF"]
    assert man["run_date"] == "2026-09-17" and man["ffmpeg"].startswith("ffmpeg")
    assert "platform" not in man  # the manifest lands in a PUBLIC bucket: no kernel/glibc fingerprint
    missing = m.build_manifest(worker_version="v", run_date=datetime(2026, 9, 17).date(), dry_run=True,
                               ffmpeg=None, fonts_dir=str(tmp_path / "nope"))
    assert missing["fonts"] == [] and missing["ffmpeg"] is None
    assert missing["fonts_error"] and "FileNotFoundError" in missing["fonts_error"]


class _TickingDT(datetime):
    """`datetime.now()` that moves an hour on every call — a clock read that leaks into the
    manifest can then never hide inside one microsecond."""

    calls = 0

    @classmethod
    def now(cls, tz=None):
        cls.calls += 1
        return datetime(2026, 9, 17, 16, 0, tzinfo=ET) + timedelta(hours=cls.calls)


def test_build_manifest_is_a_pure_function_of_image_and_run(m, monkeypatch, tmp_path):
    """The manifest bytes are content-addressed into a PUBLIC, immutable path: anything
    volatile in them mints a new object + asset row on every re-claimed attempt."""
    monkeypatch.setattr(m, "datetime", type("DT", (_TickingDT,), {"calls": 0}))
    kw = dict(worker_version="v", run_date=datetime(2026, 9, 17).date(), dry_run=True,
              ffmpeg="ffmpeg version 5.1.9", fonts_dir=str(tmp_path))
    a, b = m.build_manifest(**kw), m.build_manifest(**kw)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert "generated_at" not in a


def test_sha256_hex(m):
    assert m.sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ── the conversation with the backend ─────────────────────────────────────────


_CONTENT_TYPES = {"json": "application/json", "m4a": "audio/mp4", "mp3": "audio/mpeg",
                  "mp4": "video/mp4", "png": "image/png", "jpg": "image/jpeg"}


def _fake_narration_runner(lines, *, voice, speed, out_dir, heartbeat=None, timeout=None):
    """Stands in for the Kokoro child: timed words for exactly the narrated lines."""
    from marketing import timings as tm
    from marketing import voice as vc

    per_line = [(tm.proportional(line.split(), 0.0, 1.0), 1.0) for line in lines]
    words = tm.as_table(tm.assemble(lines, per_line))
    return vc.Narration(Path(out_dir) / "narration.wav", words, 1.0 * len(lines), speed, len(lines))


_FAKE_M4A = b"fake-m4a:narration.wav"
_FAKE_MP4 = b"fake-mp4-bytes"


def _fake_produce_video(*, workdir, specs, words, narration_seconds, audio_file, fonts_dir, logo_path,
                        threads, heartbeat, run_id, max_seconds, layout_engine):
    """Stands in for cards + captions + ffmpeg (their own tests: test_marketing_cards.py,
    test_marketing_video.py): every card shown, the full narration + disclaimer duration."""
    from marketing import voice as vc

    assert (Path(workdir) / audio_file).read_bytes() == _FAKE_M4A
    return _FAKE_MP4, narration_seconds + vc.DISCLAIMER_CARD_SECONDS, list(range(len(specs)))


@pytest.fixture(autouse=True)
def _no_real_voice(monkeypatch):
    """No test here loads torch or runs ffmpeg: the voice stage's synthesis child and encode, and
    the render's download and ffmpeg call, are faked (their own tests live in
    tests/test_marketing_voice.py, test_marketing_cards.py and test_marketing_video.py)."""
    from marketing import render as rd
    from marketing import voice as vc

    monkeypatch.setattr(vc, "run_child", _fake_narration_runner)
    monkeypatch.setattr(vc, "encode_m4a", lambda wav, out: (_FAKE_M4A, 2.0))
    monkeypatch.setattr(rd, "download", lambda url, **k: _FAKE_M4A)
    monkeypatch.setattr(rd, "produce_video", _fake_produce_video)


class FakeBackend:
    """Answers the internal API and the Storage signed-upload PUT; records every call."""

    _SCRIPT = {"hook": "h", "video_script": ["a line."], "cards": [], "carousel_slides": [],
               "disclaimer_card": "Educational only. Caydex", "outlets": ["x"]}

    def __init__(self, *, claim_reason="claimed", claim_status_codes=None, complete_status=200,
                 script_states=None, script_http_status=None,
                 script_error_code="MARKETING_SCRIPT_NOT_READY", posts_status=200,
                 posts_error_code="MARKETING_REQUEST_INVALID"):
        # The day's-script kick-and-poll answers, in order; the last one repeats.
        self.script_states: List[Dict[str, Any]] = list(script_states or [
            {"status": "generating", "source_ref": "journey:mr_market", "template_id": "checklist"},
            {"status": "accepted", "source_ref": "journey:mr_market", "template_id": "checklist",
             "script": self._SCRIPT},
        ])
        self.patches: List[Dict[str, Any]] = []
        self.calls: List[tuple] = []
        self.claim_reason = claim_reason
        self.claim_status_codes = list(claim_status_codes or [])
        self.complete_status = complete_status
        # A non-200 answered to EVERY script kick (a 4xx must not be retried).
        self.script_http_status = script_http_status
        self.script_error_code = script_error_code
        self.uploaded: Dict[str, bytes] = {}
        # Asset rows by content-addressed path, like the real ledger: re-registering the bytes
        # of a `ready` row returns it with upload=None (no second object, no second PUT).
        self.assets: Dict[str, Dict[str, Any]] = {}
        self.registered: List[Dict[str, Any]] = []
        self.claim_bodies: List[Dict[str, Any]] = []
        self.claim_headers: List[Any] = []
        self.posts_bodies: List[Dict[str, Any]] = []
        self.posts_status = posts_status
        self.posts_error_code = posts_error_code
        self.run = {"id": "run-1", "run_date": "2026-09-17", "status": "in_progress", "stage": "planned",
                    "content_class": "A", "attempts": 1, "dry_run": True, "timings": {}, "metadata": {}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == "POST" and path.endswith("/runs/claim"):
            self.claim_bodies.append(json.loads(request.content))
        if request.url.host == "sb.example":
            assert request.method == "PUT"
            assert request.headers.get("x-upsert") == "false"
            body = request.read()
            (asset,) = [a for a in self.assets.values() if path.endswith(a["storage_path"])]
            assert b'name="file"' in body and f"Content-Type: {asset['content_type']}".encode() in body
            self.uploaded[path] = body
            return httpx.Response(200, json={"Key": path})
        assert request.headers.get("x-marketing-worker-token") == "tok"
        if path.endswith("/runs/claim"):
            if self.claim_status_codes:
                return httpx.Response(self.claim_status_codes.pop(0), json={"error_code": "X", "message": "boom"})
            nonce = self.claim_bodies[-1].get("claim_nonce")
            if self.claim_reason == "claimed":
                self.run["metadata"] = {**self.run.get("metadata", {}), "claim_nonce": nonce}
            return httpx.Response(200, json={"claimed": self.claim_reason == "claimed",
                                             "reason": self.claim_reason, "run": self.run})
        # Like the real router (`require_caller_claim`): every call after the claim presents it.
        expected = f"{self.run.get('attempts')}.{(self.run.get('metadata') or {}).get('claim_nonce')}"
        self.claim_headers.append(request.headers.get("x-marketing-claim"))
        if request.headers.get("x-marketing-claim") != expected:
            return httpx.Response(422, json={"error_code": "MARKETING_REQUEST_INVALID",
                                             "message": "X-Marketing-Claim: missing or not the run's"})
        if path.endswith("/assets") and request.method == "POST":
            body = json.loads(request.content)
            self.registered.append(body)
            storage_path = f"{self.run['run_date']}/{body['kind']}-{body['sha256'][:16]}.{body['ext']}"
            asset = self.assets.get(storage_path)
            if asset is None:  # first registration wins, metadata included
                asset = {"id": f"asset-{len(self.assets) + 1}", "run_id": "run-1", "kind": body["kind"],
                         "content_type": _CONTENT_TYPES[body["ext"]], "storage_path": storage_path,
                         "sha256": body["sha256"], "status": "pending_upload",
                         "duration_seconds": body.get("duration_seconds"),
                         "metadata": body.get("metadata") or {}}
                self.assets[storage_path] = asset
            if asset["status"] == "ready":
                return httpx.Response(200, json={"asset": asset, "upload": None})
            return httpx.Response(200, json={"asset": asset, "upload": {
                "method": "PUT", "url": f"https://sb.example/object/upload/sign/marketing-media/{asset['storage_path']}?token=t",
                "token": "t", "bucket": "marketing-media", "path": asset["storage_path"],
                "content_type": asset["content_type"]}})
        if path.endswith("/assets") and request.method == "GET":
            ready = [dict(a, public_url=f"https://sb.example/object/public/marketing-media/{a['storage_path']}")
                     for a in self.assets.values() if a["status"] == "ready"]

            def verified(key, kind, role=None):
                pointer = (self.run.get("metadata") or {}).get(key)
                return pointer if any(a["id"] == pointer and a["kind"] == kind
                                      and (role is None or (a.get("metadata") or {}).get("image_role") == role)
                                      for a in ready) else None

            # Like the real read-back (run_service.read_back): the post image is a ready `card` whose
            # image_role is the post image.
            return httpx.Response(200, json={"voice_asset_id": verified("voice_asset_id", "audio"),
                                             "video_asset_id": verified("video_asset_id", "video"),
                                             "image_asset_id": verified("image_asset_id", "card", "post_image"),
                                             "assets": ready})
        if path.endswith("/posts") and request.method == "POST":
            if self.posts_status != 200:
                return httpx.Response(self.posts_status, json={"error_code": self.posts_error_code,
                                                               "message": "no posts"})
            body = json.loads(request.content)
            self.posts_bodies.append(body)
            return httpx.Response(200, json={"posts": [
                {"id": f"post-{i}", "run_id": "run-1", "platform": sp["platform"], "format": sp["format"],
                 "status": "pending_review", "caption": "server copy", "asset_ids": sp.get("asset_ids", []),
                 "idempotency_key": f"2026-09-17:{sp['platform']}:{sp['format']}", "metadata": {}}
                for i, sp in enumerate(body["posts"])]})
        if path.endswith("/complete"):
            if self.complete_status != 200:
                return httpx.Response(self.complete_status, json={"error_code": "MARKETING", "message": "not in bucket"})
            asset_id = path.rsplit("/", 2)[-2]
            (asset,) = [a for a in self.assets.values() if a["id"] == asset_id]
            asset["status"] = "ready"
            return httpx.Response(200, json={"asset": asset})
        if path.endswith("/script") and request.method == "POST":
            if self.script_http_status is not None:
                return httpx.Response(self.script_http_status,
                                      json={"error_code": self.script_error_code, "message": "no"})
            state = self.script_states.pop(0) if len(self.script_states) > 1 else self.script_states[0]
            return httpx.Response(200, json=state)
        if request.method == "PATCH":
            body = json.loads(request.content)
            self.patches.append(body)
            self.run.update({k: v for k, v in body.items() if k in ("status", "stage", "last_error")})
            if body.get("metadata"):
                self.run["metadata"] = {**self.run.get("metadata", {}), **body["metadata"]}
            return httpx.Response(200, json=self.run)
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "message": path})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MARKETING_API_BASE_URL", "https://backend.example")
    monkeypatch.setenv("MARKETING_WORKER_TOKEN", "tok")
    monkeypatch.setenv("MARKETING_FORCE", "1")
    monkeypatch.setenv("MARKETING_RUN_DATE", "2026-09-17")
    monkeypatch.setenv("MARKETING_FONTS_DIR", str(_PKG / "assets" / "fonts"))
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("MARKETING_RENDER_THREADS", raising=False)


def _wire(m, monkeypatch, backend: FakeBackend):
    transport = httpx.MockTransport(backend.handler)

    class Shim:
        Client = functools.partial(httpx.Client, transport=transport)
        TransportError = httpx.TransportError

    monkeypatch.setattr(m, "httpx", Shim)
    monkeypatch.setattr(m, "ffmpeg_version", lambda: "ffmpeg version 5.1.9 (fake)")
    # This venv has no torch/kokoro: report the image as voice-ready (the check itself is
    # tested in test_the_voice_readiness_check_names_every_missing_piece).
    monkeypatch.setattr(m, "voice_readiness", lambda fonts_dir: {"ready": True, "problems": []})
    monkeypatch.setattr(m, "render_readiness", lambda fonts_dir: {"ready": True, "problems": [], "raqm": True})
    monkeypatch.setattr(m, "time", _FastTime())


class _FastTime:
    """No real sleeping in the retry backoff."""
    monotonic = staticmethod(__import__("time").monotonic)

    @staticmethod
    def sleep(_):
        return None


def test_happy_path_on_a_text_only_day_records_posts_without_any_media(m, monkeypatch, env):
    be = FakeBackend()
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    tails = [(meth, p.rsplit("/", 1)[-1]) for meth, p in be.calls]
    assert tails[:2] == [("POST", "claim"), ("POST", "assets")]
    assert tails[2][0] == "PUT" and tails[2][1].startswith("manifest-") and tails[2][1].endswith(".json")
    assert tails[3:9] == [
        ("POST", "complete"), ("PATCH", "run-1"),          # preflight
        ("POST", "script"), ("PATCH", "run-1"),            # stage `selected` (the first kick)
        ("POST", "script"), ("PATCH", "run-1"),            # stage `scripted` (poll → accepted)
    ]
    # The script's only outlet is X (text): no narration, no render (both stages checkpoint with
    # nothing to do), one text post, and the run closes media_ready — the publisher's from here.
    assert tails[9:] == [("PATCH", "run-1"),                              # voiced (no media needed)
                         ("PATCH", "run-1"),                              # rendered (no video outlet)
                         ("POST", "posts"), ("PATCH", "run-1"),           # posts, assets_ready
                         ("PATCH", "run-1")]                              # close
    assert [p.get("stage") for p in be.patches if p.get("stage")] == [
        "selected", "scripted", "voiced", "rendered", "assets_ready"]
    assert be.posts_bodies == [{"posts": [{"platform": "x", "format": "text"}]}]
    assert not [b for b in be.registered if b["kind"] in ("audio", "video")]
    # Every media checkpoint carries its peak-memory reading (the 4 GB sizing is checked on it).
    voiced = next(p for p in be.patches if p.get("stage") == "voiced")
    assert "metadata" not in voiced and voiced["timings"]["voiced_self_maxrss_mb"] > 0
    assert be.run["status"] == "media_ready" and "skip_reason" not in be.run["metadata"]
    # The manifest that went up is real JSON describing the image.
    (body,) = [b for p, b in be.uploaded.items() if "/manifest-" in p]
    start, end = body.find(b"{"), body.rfind(b"}") + 1
    manifest = json.loads(body[start:end])
    assert manifest["worker_version"] == "phase4" and manifest["run_date"] == "2026-09-17"
    assert manifest["stages_implemented"] == ["selected", "scripted", "voiced", "rendered", "assets_ready"]
    assert manifest["ffmpeg"].startswith("ffmpeg")


def test_not_claimed_exits_zero_without_touching_anything_else(m, monkeypatch, env):
    for reason in ("already_done", "in_progress", "media_ready"):
        be = FakeBackend(claim_reason=reason)
        _wire(m, monkeypatch, be)
        assert m.main() == 0
        assert [meth for meth, _ in be.calls] == ["POST"], reason


def test_before_the_window_only_tries_to_resume_yesterday_and_never_creates(m, monkeypatch, env):
    monkeypatch.delenv("MARKETING_FORCE")
    monkeypatch.delenv("MARKETING_RUN_DATE")
    monkeypatch.setenv("MARKETING_RUN_HOUR_ET", "16")

    class FrozenDT(m.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 17, 9, 0, tzinfo=ET)

    monkeypatch.setattr(m, "datetime", FrozenDT)
    be = FakeBackend(claim_reason="no_run")
    be.run = None  # type: ignore[assignment]
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert [meth for meth, _ in be.calls] == ["POST"]
    (body,) = be.claim_bodies
    assert body["resume_only"] is True and body["run_date"] == "2026-09-16"
    assert len(body["claim_nonce"]) == 32


def test_every_claim_carries_a_per_process_nonce(m, monkeypatch, env):
    be = FakeBackend()
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    (body,) = be.claim_bodies
    assert body["resume_only"] is False and len(body["claim_nonce"]) == 32


def test_httpx_request_lines_with_the_signed_token_are_not_logged(m, monkeypatch, env, caplog):
    """httpx logs every request URL at INFO; the signed-upload URL carries ?token=."""
    import logging as _logging

    be = FakeBackend()
    _wire(m, monkeypatch, be)
    with caplog.at_level(_logging.DEBUG):
        assert m.main() == 0
    assert not any("token=" in rec.getMessage() for rec in caplog.records)
    assert _logging.getLogger("httpx").level == _logging.WARNING


def test_lowercase_log_level_does_not_crash_the_worker(monkeypatch):
    monkeypatch.setenv("MARKETING_LOG_LEVEL", "debug")
    _load()  # module import applies basicConfig


def test_transient_5xx_on_claim_is_retried_then_succeeds(m, monkeypatch, env):
    be = FakeBackend(claim_status_codes=[503, 502])
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert [p.rsplit("/", 1)[-1] for meth, p in be.calls][:3] == ["claim", "claim", "claim"]


def test_persistent_5xx_gives_up_with_exit_1(m, monkeypatch, env):
    be = FakeBackend(claim_status_codes=[503, 503, 503, 503])
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert len(be.calls) == 3  # _HTTP_ATTEMPTS, then stop


@pytest.mark.parametrize("status", [409, 422])
def test_a_stage_failure_is_recorded_on_the_run_and_exits_1(m, monkeypatch, env, status):
    """A 4xx is terminal for the stage — 409 (MARKETING_ASSET_MISSING / SCRIPT_NOT_READY) is
    documented as "the worker must NOT retry the same call". The fake answers the SAME 4xx
    every time, so a retrying client would show up as three `complete` calls."""
    be = FakeBackend(complete_status=status)
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert be.run["status"] == "failed"
    tails = [(meth, p.rsplit("/", 1)[-1]) for meth, p in be.calls]
    assert [t for t in tails if t[0] != "PUT"] == [
        ("POST", "claim"), ("POST", "assets"), ("POST", "complete"), ("PATCH", "run-1"),
    ], tails
    assert sum(p.endswith("/complete") for _, p in be.calls) == 1
    # the failure PATCH carried the error text — the status, not a retry-exhaustion message
    last_error = be.patches[-1]["last_error"]
    assert f"-> {status}" in last_error and "attempts" not in last_error


@pytest.mark.parametrize("status", [401, 403, 409, 422])
def test_a_4xx_on_claim_is_not_retried(m, monkeypatch, env, status):
    be = FakeBackend(claim_status_codes=[status] * (m._HTTP_ATTEMPTS + 1))
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert len(be.calls) == 1, be.calls


def test_a_4xx_on_the_script_kick_is_not_retried(m, monkeypatch, env):
    be = FakeBackend(script_http_status=409)
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert sum(p.endswith("/script") for _, p in be.calls) == 1
    assert be.run["status"] == "failed" and "-> 409" in be.patches[-1]["last_error"]


def test_a_run_no_longer_held_exits_0_without_a_failure_write(m, monkeypatch, env, caplog):
    """409 MARKETING_RUN_NOT_HELD: the run was closed or re-claimed under this tick. A failure
    PATCH would be refused with the same 409 (and log a misleading "could not record failure"),
    so the tick stops quietly — but loudly enough to be seen: one WARNING naming the run."""
    be = FakeBackend(script_http_status=409, script_error_code="MARKETING_RUN_NOT_HELD")
    _wire(m, monkeypatch, be)
    with caplog.at_level(logging.WARNING, logger=m.logger.name):
        assert m.main() == 0
    assert sum(p.endswith("/script") for _, p in be.calls) == 1
    assert not [p for p in be.patches if p.get("status") == "failed"], be.patches
    assert any("no longer held" in r.getMessage() and "run-1" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_worker_api_error_carries_the_structured_code(m, monkeypatch, env):
    be = FakeBackend(script_http_status=409, script_error_code="MARKETING_SCRIPT_NOT_READY")
    _wire(m, monkeypatch, be)
    client = m.BackendClient("https://backend.example", "tok")
    be.run["metadata"] = {"claim_nonce": "ab" * 16}
    client.hold(be.run, "ab" * 16)          # as main() does after its claim
    try:
        with pytest.raises(m.WorkerAPIError) as info:
            client.kick_script("run-1")
    finally:
        client.close()
    assert info.value.status == 409 and info.value.error_code == "MARKETING_SCRIPT_NOT_READY"


def test_missing_env_is_exit_1_before_any_call(m, monkeypatch):
    monkeypatch.delenv("MARKETING_API_BASE_URL", raising=False)
    monkeypatch.delenv("MARKETING_WORKER_TOKEN", raising=False)
    assert m.main() == 1


def test_signed_upload_sends_apikey_only_when_configured(m, monkeypatch, env):
    seen: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["apikey"] = request.headers.get("apikey")
        seen["upsert"] = request.headers.get("x-upsert")
        return httpx.Response(200, json={"Key": "k"})

    class Shim:
        Client = functools.partial(httpx.Client, transport=httpx.MockTransport(handler))
        TransportError = httpx.TransportError

    monkeypatch.setattr(m, "httpx", Shim)
    up = {"url": "https://sb.example/object/upload/sign/marketing-media/x.json?token=t",
          "path": "x.json", "content_type": "application/json"}
    m.upload_signed(up, b"{}")
    assert seen == {"apikey": None, "upsert": "false"}
    m.upload_signed(up, b"{}", apikey="pk")
    assert seen["apikey"] == "pk"


def test_signed_upload_409_means_already_there_and_is_not_an_error(m, monkeypatch, env):
    class Shim:
        Client = functools.partial(httpx.Client, transport=httpx.MockTransport(
            lambda r: httpx.Response(409, json={"statusCode": "409", "error": "Duplicate"})))
        TransportError = httpx.TransportError

    monkeypatch.setattr(m, "httpx", Shim)
    m.upload_signed({"url": "https://sb.example/u?token=t", "path": "x.json",
                     "content_type": "application/json"}, b"{}")  # no raise


def test_signed_upload_failure_is_loud(m, monkeypatch, env):
    class Shim:
        Client = functools.partial(httpx.Client, transport=httpx.MockTransport(
            lambda r: httpx.Response(413, text="Payload too large")))
        TransportError = httpx.TransportError

    monkeypatch.setattr(m, "httpx", Shim)
    with pytest.raises(m.WorkerAPIError, match="413"):
        m.upload_signed({"url": "https://sb.example/u?token=t", "path": "x.json",
                         "content_type": "application/json"}, b"{}")


def test_railway_watch_patterns_are_repo_rooted():
    """Railway evaluates watchPatterns from the REPO root even with a Root Directory set, so
    an un-prefixed pattern never matches and every push is skipped as 'no changes'."""
    toml = (_PKG / "railway.toml").read_text()
    body = "\n".join(l for l in toml.splitlines() if not l.strip().startswith("#"))
    m_ = re.search(r"watchPatterns\s*=\s*\[(.*?)\]", body, re.S)
    assert m_, "watchPatterns missing"
    patterns = re.findall(r'"([^"]+)"', m_.group(1))
    assert patterns and all(p.startswith("/backend/") for p in patterns), patterns
    assert "cronSchedule" in body and "healthcheckPath" not in body


# ── Phase 2: the day's script ─────────────────────────────────────────────────


class _Clock:
    """A clock that advances only when the worker sleeps, so a poll budget is testable."""

    def __init__(self):
        self.t = 1000.0
        self.slept: List[float] = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _closing_patch(be: FakeBackend) -> Dict[str, Any]:
    return be.patches[-1]


def test_rest_day_is_skipped_before_any_stage_checkpoint(m, monkeypatch, env):
    be = FakeBackend(script_states=[{"status": "rest_day"}])
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "rest_day"
    assert not [p for p in be.patches if p.get("stage")]  # a skip is not a finished stage
    assert sum(1 for _, p in be.calls if p.endswith("/script")) == 1


def test_rejected_script_skips_the_day_with_a_reason(m, monkeypatch, env):
    be = FakeBackend(script_states=[
        {"status": "generating", "source_ref": "journey:x"},
        {"status": "rejected", "source_ref": "journey:x", "reason": "content",
         "violations": ["person_named"]},
    ])
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "content_rejected"
    assert [p.get("stage") for p in be.patches if p.get("stage")] == ["selected"]


def test_deferred_writer_ends_the_tick_failed_for_the_next_one_to_resume(m, monkeypatch, env):
    be = FakeBackend(script_states=[
        {"status": "generating"}, {"status": "deferred", "retry_after_seconds": 1800},
    ])
    _wire(m, monkeypatch, be)
    assert m.main() == 0  # a deferral is not an error of this tick
    closing = _closing_patch(be)
    assert closing["status"] == "failed" and closing["last_error"].startswith("deferred:")


def test_poll_budget_is_bounded_and_ends_deferred(m, monkeypatch, env):
    be = FakeBackend(script_states=[{"status": "generating"}])
    _wire(m, monkeypatch, be)
    clock = _Clock()
    monkeypatch.setattr(m, "time", clock)
    assert m.main() == 0
    polls = sum(1 for _, p in be.calls if p.endswith("/script"))
    assert 2 <= polls <= m.SCRIPT_POLL_BUDGET_SECONDS // m.SCRIPT_POLL_SECONDS + 2
    assert all(s == m.SCRIPT_POLL_SECONDS for s in clock.slept)
    assert _closing_patch(be)["status"] == "failed"


def test_resume_after_selected_goes_straight_to_polling(m, monkeypatch, env):
    be = FakeBackend(script_states=[{"status": "accepted", "script": FakeBackend._SCRIPT}])
    be.run["stage"] = "selected"
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert [p.get("stage") for p in be.patches if p.get("stage")] == [
        "scripted", "voiced", "rendered", "assets_ready"]
    assert be.run["status"] == "media_ready"


def test_resume_after_scripted_re_derives_the_script_once_and_voices_it(m, monkeypatch, env):
    """A resume past `scripted` skips the stage that filled ctx["script"]: `run_pipeline`
    re-derives it with ONE idempotent kick (never a poll) before the voice stage runs."""
    be = FakeBackend(script_states=[{"status": "accepted", "script": FakeBackend._SCRIPT}])
    be.run["stage"] = "scripted"
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert sum(p.endswith("/script") for _, p in be.calls) == 1
    assert [p.get("stage") for p in be.patches if p.get("stage")] == ["voiced", "rendered", "assets_ready"]
    assert be.run["status"] == "media_ready"


def test_resume_after_voiced_renders_and_posts_without_voicing_again(m, monkeypatch, env):
    """Past `voiced`, the script is re-derived with ONE kick (the render and the posts need it);
    the narration is never synthesised again."""
    be = FakeBackend(script_states=[{"status": "accepted", "script": FakeBackend._SCRIPT}])
    be.run["stage"] = "voiced"
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert sum(p.endswith("/script") for _, p in be.calls) == 1
    assert not [b for b in be.registered if b["kind"] == "audio"]
    assert [p.get("stage") for p in be.patches if p.get("stage")] == ["rendered", "assets_ready"]
    assert be.run["status"] == "media_ready"


def test_resume_after_assets_ready_only_closes(m, monkeypatch, env):
    be = FakeBackend()
    be.run["stage"] = "assets_ready"
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert not any(p.endswith(("/script", "/posts")) for _, p in be.calls)
    assert be.run["status"] == "media_ready"


def test_unexpected_script_status_fails_the_run_loudly(m, monkeypatch, env):
    be = FakeBackend(script_states=[{"status": "selected"}, {"status": "banana"}])
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert _closing_patch(be)["status"] == "failed"


# ── a `rejected` day carries WHY (contract: the kick's `reason`) ───────────────


@pytest.mark.parametrize("reason, skip_reason", [
    ("content", "content_rejected"),
    ("writer_unavailable", "writer_unavailable"),
    ("empty_pool", "empty_pool"),
    ("source_ineligible", "source_ineligible"),
])
@pytest.mark.parametrize("after_polling", [False, True])
def test_the_rejection_reason_becomes_the_skip_reason(m, monkeypatch, env, caplog, reason,
                                                       skip_reason, after_polling):
    """A writer outage exhausts the generation cap too; recording that as `content_rejected`
    pointed an operator at the prompts instead of the key or the model."""
    import logging as _logging

    rejected = {"status": "rejected", "source_ref": "money_moves:x", "reason": reason, "violations": []}
    states = [{"status": "generating", "source_ref": "money_moves:x"}, rejected] if after_polling else [rejected]
    be = FakeBackend(script_states=states)
    _wire(m, monkeypatch, be)
    with caplog.at_level(_logging.INFO, logger="marketing_worker"):
        assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == skip_reason
    assert [p.get("stage") for p in be.patches if p.get("stage")] == (["selected"] if after_polling else [])
    recs = [r for r in caplog.records if "REJECTED" in r.getMessage()]
    assert len(recs) == 1 and f"skip_reason={skip_reason}" in recs[0].getMessage()
    expected = _logging.ERROR if reason in ("writer_unavailable", "empty_pool") else _logging.WARNING
    assert recs[0].levelno == expected
    assert not any("rejection reason" in r.getMessage() for r in caplog.records)


def test_the_worker_maps_exactly_the_server_rejection_reasons(m):
    """Duplicated on purpose (the worker imports nothing from app.*), like RUN_STAGES: a reason
    the server adds must get a skip_reason here, or it is recorded as `content_rejected`."""
    from app.schemas.marketing import SCRIPT_REJECT_REASONS

    assert set(m.REJECTION_SKIP_REASONS) == set(SCRIPT_REJECT_REASONS)
    assert m.REJECTION_SKIP_REASONS["content"] == m.DEFAULT_REJECTION_SKIP_REASON


@pytest.mark.parametrize("reason, skip_reason", [
    ("content", "content_rejected"), ("writer_unavailable", "writer_unavailable"),
    ("empty_pool", "empty_pool"), ("source_ineligible", "source_ineligible"),
])
def test_the_reason_survives_the_real_wire_schema(m, monkeypatch, env, reason, skip_reason):
    """The endpoint answers `ScriptKickResponse.model_validate(state)` under a response_model,
    and pydantic DROPS an undeclared key — so a server that computes `reason` but whose schema
    does not declare it would label every outage `content_rejected` again, silently."""
    from app.schemas.marketing import ScriptKickResponse

    body = ScriptKickResponse.model_validate(
        {"status": "rejected", "source_ref": "money_moves:x", "reason": reason, "violations": []}
    ).model_dump(mode="json")
    be = FakeBackend(script_states=[body])
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert be.run["metadata"]["skip_reason"] == skip_reason


_MISSING = object()


@pytest.mark.parametrize("reason", [_MISSING, None, "banana", "CONTENT", "", 7, {"x": 1}, ["content"],
                                    "x" * 5000])
def test_a_missing_or_unknown_rejection_reason_is_content_rejected_and_warned(m, monkeypatch, env,
                                                                              caplog, reason):
    """Backward compatible with a web service that sends no `reason`, never silent about it —
    and a server string is never written to the ledger verbatim."""
    import logging as _logging

    rejected = {"status": "rejected", "source_ref": "journey:x", "violations": ["person_named"]}
    if reason is not _MISSING:
        rejected["reason"] = reason
    be = FakeBackend(script_states=[rejected])
    _wire(m, monkeypatch, be)
    with caplog.at_level(_logging.INFO, logger="marketing_worker"):
        assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "content_rejected"
    (warn,) = [r for r in caplog.records if "rejection reason" in r.getMessage()]
    assert warn.levelno == _logging.WARNING
    shown = None if reason is _MISSING else reason
    assert repr(shown)[:60] in warn.getMessage() and len(warn.getMessage()) < 400


# ── a resumed run re-derives what the stages it skipped produced ──────────────


def _with_a_later_stage(m, monkeypatch) -> List[Any]:
    """Append the Phase-3 `voiced` stage the way it will naturally be written: reading the
    script the `scripted` stage produced out of ctx."""
    seen: List[Any] = []

    def stage_voice(api, run, ctx):
        seen.append(ctx["script"])

    monkeypatch.setattr(m, "MEDIA_STAGES",
                        [(name, stage_voice if name == "voiced" else fn) for name, fn in m.MEDIA_STAGES])
    return seen


@pytest.mark.parametrize("checkpoint, kicks", [("planned", 2), ("selected", 2), ("scripted", 1)])
def test_a_stage_after_scripted_gets_the_script_on_a_fresh_run_and_on_every_resume(
        m, monkeypatch, env, checkpoint, kicks):
    """`ctx` is per-process and a resume SKIPS the stage that filled it: without re-deriving,
    a resume from `scripted` KeyErrors in voice on every re-claim until the attempts run out,
    with an accepted script sitting on the server."""
    seen = _with_a_later_stage(m, monkeypatch)
    be = FakeBackend()
    be.run["stage"] = checkpoint
    if checkpoint == "scripted":  # the kick is idempotent: an ACCEPTED row returns its script
        be.script_states = [{"status": "accepted", "script": FakeBackend._SCRIPT}]
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert seen == [FakeBackend._SCRIPT]
    assert sum(p.endswith("/script") for _, p in be.calls) == kicks
    assert "voiced" in [p.get("stage") for p in be.patches if p.get("stage")]
    assert be.run["status"] == "media_ready"


@pytest.mark.parametrize("state", [
    {"status": "generating"},
    {"status": "deferred", "retry_after_seconds": 60},
    {"status": "rejected", "reason": "content"},
    {"status": "rest_day"},
    {"status": "accepted"},
    {"status": "accepted", "script": {}},
])
def test_past_the_checkpoint_anything_but_an_accepted_script_fails_loudly(m, monkeypatch, env, state):
    """After `scripted` the script is accepted and immutable: another answer is an
    inconsistency. Fail the tick — never re-enter the poll loop, never close the day skipped."""
    seen = _with_a_later_stage(m, monkeypatch)
    be = FakeBackend(script_states=[state])
    be.run["stage"] = "scripted"
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert seen == []
    assert sum(p.endswith("/script") for _, p in be.calls) == 1  # no polling
    closing = _closing_patch(be)
    assert closing["status"] == "failed" and "past the `scripted` checkpoint" in closing["last_error"]
    assert "skip_reason" not in be.run["metadata"]


# ── the preflight manifest is content-addressed ───────────────────────────────


def test_a_reclaimed_run_reuses_its_manifest_instead_of_minting_a_new_object(m, monkeypatch, env):
    """Two ticks of one day (the first deferred, the second re-claimed) must register ONE
    manifest path and PUT it ONCE — the public bucket is immutable and never pruned."""
    monkeypatch.setattr(m, "datetime", type("DT", (_TickingDT,), {"calls": 0}))
    be = FakeBackend(script_states=[{"status": "generating"}, {"status": "deferred", "retry_after_seconds": 1}])
    _wire(m, monkeypatch, be)
    assert m.main() == 0 and be.run["status"] == "failed"
    be.run["status"] = "in_progress"  # the next hourly tick re-claims it
    be.script_states = [{"status": "accepted", "script": FakeBackend._SCRIPT}]
    assert m.main() == 0

    manifests = [b for b in be.registered if b["kind"] == "manifest"]
    assert len(manifests) == 2 and manifests[0]["sha256"] == manifests[1]["sha256"]
    manifest_assets = [a for a in be.assets.values() if a["kind"] == "manifest"]
    assert len(manifest_assets) == 1
    assert sum("/manifest-" in p for p in be.uploaded) == 1
    manifest_completes = [p for _, p in be.calls if p.endswith("/complete")
                          and p.rsplit("/", 2)[-2] == manifest_assets[0]["id"]]
    assert len(manifest_completes) == 1  # tick 2 took the "already ready" branch
    # the time is not lost — it rides in the (unhashed) asset metadata, first registration wins
    (asset,) = manifest_assets
    assert asset["status"] == "ready" and asset["metadata"]["generated_at"].startswith("2026-09-17T")
    assert manifests[0]["metadata"]["generated_at"] != manifests[1]["metadata"]["generated_at"]
    assert be.run["status"] == "media_ready"


def test_worker_deadlines_fit_inside_the_backend_stale_window(m):
    """A live tick must never look abandoned to `decide_claim` (liveness = updated_at), and one
    poll session must outlast a crashed generation's lease so the next kick can take over."""
    from app.config import Settings
    from app.services.marketing import script_service

    stale = Settings.model_fields["MARKETING_RUN_STALE_SECONDS"].default
    assert m.WORKER_DEADLINE_SECONDS < stale
    assert m.SCRIPT_POLL_BUDGET_SECONDS <= m.WORKER_DEADLINE_SECONDS
    assert m.SCRIPT_POLL_BUDGET_SECONDS > script_service.LEASE_SECONDS
    assert m.SCRIPT_POLL_SECONDS < script_service.LEASE_SECONDS


def _stale_default() -> int:
    from app.config import Settings

    return Settings.model_fields["MARKETING_RUN_STALE_SECONDS"].default


def test_every_media_stage_fits_its_worst_case_inside_the_start_margin(m):
    """A stage starts only with STAGE_START_MARGIN_SECONDS of the tick left, so its WORST CASE must
    fit in it — or a started stage runs past WORKER_DEADLINE_SECONDS. The voice stage's did not
    (2×480 s synthesis + a 120 s encode against a 600 s margin) until both attempts shared one
    budget (review 2026-09-29)."""
    from marketing import render, voice

    call = m.BACKEND_CALL_WORST_SECONDS
    assert call == m._HTTP_ATTEMPTS * m.BACKEND_TIMEOUT_SECONDS + 2 + 4 + 6
    # Per stage: its own work, its backend calls (each can take the full retry chain), one
    # heartbeat that blocks as long, and the checkpoint PATCH after it — starting at the LATEST
    # moment the stage may start — must end inside the stale window (review 2026-09-29: the old
    # pin counted neither the calls nor the heartbeat, and promised the 30-min deadline).
    worst = {
        "voiced": voice.worst_case_seconds(m.UPLOAD_TIMEOUT_SECONDS) + 3 * call,   # list, register, complete
        # list + (register, complete) for the video AND the post image (drop 1)
        "rendered": render.worst_case_seconds(m.UPLOAD_TIMEOUT_SECONDS) + 5 * call,
        "assets_ready": 2 * call,                                                 # read-back, create_posts
        "selected": call,
        # stage_script polls until min(start + SCRIPT_POLL_BUDGET, t0 + DEADLINE - 60), so at the
        # latest start its polling is bounded by its margin, plus the last kick in flight.
        "scripted": min(m.SCRIPT_POLL_BUDGET_SECONDS, m.STAGE_START_MARGINS["scripted"] - 60) + call,
    }
    assert set(worst) == set(m.STAGE_START_MARGINS) == {name for name, _fn in m.MEDIA_STAGES}
    stale = _stale_default()
    for name, work in worst.items():
        latest_start = m.WORKER_DEADLINE_SECONDS - m.STAGE_START_MARGINS[name]
        end = latest_start + work + call + call        # + one stuck heartbeat + the checkpoint
        assert end < stale, (name, end, stale)
    # The media stages still keep the full margin their own work needs.
    assert voice.worst_case_seconds(m.UPLOAD_TIMEOUT_SECONDS) <= m.STAGE_START_MARGINS["voiced"]
    assert render.worst_case_seconds(m.UPLOAD_TIMEOUT_SECONDS) <= m.STAGE_START_MARGINS["rendered"]


def test_a_one_call_stage_is_not_deferred_by_the_media_stages_margin(m, monkeypatch, env):
    """19 minutes into the tick the posts stage (one read-back, one create_posts) still runs: gating
    it on the media stages' 12 minutes closed a finished day `failed` for a one-second stage."""
    be = _video_backend()
    be.run["stage"] = "rendered"
    video = {"id": "asset-v", "run_id": "run-1", "kind": "video", "status": "ready",
             "storage_path": "2026-09-17/video-x.mp4", "sha256": "0" * 64, "content_type": "video/mp4",
             "metadata": {}}
    be.assets[video["storage_path"]] = video
    be.run["metadata"] = {"video_asset_id": "asset-v"}
    _wire(m, monkeypatch, be)
    clock = {"t": 0.0}

    class Late(_FastTime):
        @staticmethod
        def monotonic():
            clock["t"] += 19 * 60 / 4                      # every reading moves ~5 min on
            return clock["t"]

    monkeypatch.setattr(m, "time", Late())
    assert m.main() == 0
    assert be.run["status"] == "media_ready" and len(be.posts_bodies) == 1
    assert m.stage_start_margin("assets_ready", {}) < m.STAGE_START_MARGIN_SECONDS
    assert m.stage_start_margin("rendered", {"script": {"outlets": ["x"]}}) == m.QUICK_STAGE_MARGIN_SECONDS
    assert m.stage_start_margin("rendered", {"script": {"outlets": ["tiktok"]}}) == m.STAGE_START_MARGIN_SECONDS



# ── the caller-claim header and the stage deadline (rules marketing.md §2) ─────


def test_every_call_after_the_claim_presents_it(m, monkeypatch, env):
    be = FakeBackend()
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    nonce = be.claim_bodies[0]["claim_nonce"]
    assert len(nonce) == 32 and all(c in "0123456789abcdef" for c in nonce)
    assert be.claim_headers and set(be.claim_headers) == {f"1.{nonce}"}
    # …and the claim itself carried none (it is how one is obtained).
    assert len(be.claim_headers) == len([c for c in be.calls
                                         if c[0] != "PUT" and not c[1].endswith("/runs/claim")])


def test_a_zombie_refused_by_the_claim_fence_exits_zero_without_a_failure_write(m, monkeypatch, env):
    """The server fences every write on the caller's claim; a 409 MARKETING_RUN_NOT_HELD means
    another tick holds the run now. Nothing is ours to record — no failure PATCH."""
    be = FakeBackend(script_http_status=409, script_error_code="MARKETING_RUN_NOT_HELD")
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert not [p for p in be.patches if p.get("status") == "failed"]


def test_a_stage_never_starts_too_late_in_the_tick(m, monkeypatch, env):
    """A stage may start only with STAGE_START_MARGIN_SECONDS of the tick left, so no stage can
    outlive MARKETING_RUN_STALE_SECONDS and meet a re-claimer mid-write."""
    be = FakeBackend()
    _wire(m, monkeypatch, be)
    clock = {"t": 1000.0}

    class LateTime(_FastTime):
        @staticmethod
        def monotonic():
            clock["t"] += m.WORKER_DEADLINE_SECONDS  # every reading is far past the budget
            return clock["t"]

    monkeypatch.setattr(m, "time", LateTime())
    assert m.main() == 0
    assert not [p for p in be.patches if p.get("stage")], "a stage started past the deadline"
    assert any(p.get("status") == "failed" and "too late to start" in (p.get("last_error") or "")
               for p in be.patches)
    assert m.STAGE_START_MARGIN_SECONDS < m.WORKER_DEADLINE_SECONDS



def test_the_voice_readiness_check_names_every_missing_piece(m, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    out = m.voice_readiness(str(tmp_path / "nofonts"))
    assert out["ready"] is False
    assert any("Kokoro weights" in p for p in out["problems"])
    assert any("Inter-Bold.ttf" in p for p in out["problems"])
    snap = tmp_path / "hf" / "hub" / "models--hexgrad--Kokoro-82M" / "snapshots" / "abc123" / "voices"
    snap.mkdir(parents=True)
    fonts = _PKG / "assets" / "fonts"
    out = m.voice_readiness(str(fonts))
    assert any("af_heart" in p and "not baked" in p for p in out["problems"])   # no voice file yet
    (snap / "af_heart.pt").write_bytes(b"x")
    out = m.voice_readiness(str(fonts))
    assert not any("Kokoro weights" in p or "Inter-Bold" in p or "not baked" in p for p in out["problems"])
    monkeypatch.setenv("MARKETING_TTS_VOICE", "am_michael")       # configured but never baked
    out = m.voice_readiness(str(fonts))
    assert any("am_michael" in p for p in out["problems"])



# ── Phase 4: the render and the posts (2026-09-29) ────────────────────────────


_VIDEO_SCRIPT = {
    "hook": "Meet your moody business partner.",
    "video_script": ["Every day he names a price.", "His price follows his mood."],
    "cards": [{"title": "The partner", "body": "He names a price every day."},
              {"title": "The lesson", "body": "His mood is not the business."}],
    "carousel_slides": [],
    "disclaimer_card": "Educational, impersonal information — not investment advice. Caydex · Sep 17, 2026",
    "outlets": ["tiktok", "x"],
}


def _video_backend(**kw) -> FakeBackend:
    return FakeBackend(script_states=[{"status": "accepted", "source_ref": "journey:mr_market",
                                       "template_id": "case_story", "script": _VIDEO_SCRIPT}], **kw)


def test_a_video_day_renders_registers_what_it_drew_and_records_the_posts(m, monkeypatch, env):
    be = _video_backend()
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    (video,) = [a for a in be.assets.values() if a["kind"] == "video"]
    audio = next(a for a in be.assets.values() if a["kind"] == "audio")
    md = video["metadata"]
    # Every string the cards drew — the script's own card text, its disclaimer card, the end card —
    # and the narration its captions burn (the server checks both, run_service._check_onscreen_text).
    for text in ("The partner", "He names a price every day.", "The lesson",
                 "His mood is not the business.", _VIDEO_SCRIPT["disclaimer_card"], "caydexinvest.com"):
        assert text in md["onscreen_text"], text
    assert "Meet your moody business partner." not in md["onscreen_text"]  # the hook is caption-only
    assert md["voice_asset_id"] == audio["id"] and len(md["render_key"]) == 64
    assert md["template_id"] == "case_story"
    assert video["status"] == "ready" and any("/video-" in p for p in be.uploaded)
    rendered = next(p for p in be.patches if p.get("stage") == "rendered")
    assert rendered["metadata"] == {"video_asset_id": video["id"]}      # pointer in the SAME PATCH
    assert be.posts_bodies == [{"posts": [
        {"platform": "tiktok", "format": "video", "asset_ids": [video["id"]]},
        {"platform": "x", "format": "text"}]}]
    assert be.run["status"] == "media_ready"
    # the registered duration is narration + the disclaimer card (never cut by -shortest)
    from marketing import voice as vc

    reg = next(b for b in be.registered if b["kind"] == "video")
    assert reg["duration_seconds"] == round(audio["duration_seconds"] + vc.DISCLAIMER_CARD_SECONDS, 3)


def test_a_matching_ready_video_is_reused_without_rendering_again(m, monkeypatch, env):
    be = _video_backend()
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    from marketing import render as rd

    calls = []
    monkeypatch.setattr(rd, "produce_video", lambda **k: calls.append(k) or (_ for _ in ()).throw(AssertionError))
    # the next day's tick re-claims the SAME run after a lost close (resume from `voiced`)
    be.run.update({"status": "in_progress", "stage": "voiced"})
    be.run["metadata"].pop("video_asset_id")
    be.script_states = [{"status": "accepted", "script": _VIDEO_SCRIPT}]
    assert m.main() == 0
    assert calls == [] and len([a for a in be.assets.values() if a["kind"] == "video"]) == 1
    assert be.run["status"] == "media_ready"


def test_narration_bytes_that_do_not_match_their_row_fail_the_run(m, monkeypatch, env):
    from marketing import render as rd

    be = _video_backend()
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(rd, "download", lambda url, **k: b"someone else's audio")
    assert m.main() == 1
    closing = _closing_patch(be)
    assert closing["status"] == "failed" and "sha256" in closing["last_error"]
    assert not [a for a in be.assets.values() if a["kind"] == "video"] and be.posts_bodies == []


def test_a_glyph_the_font_cannot_draw_skips_the_day_instead_of_burning_attempts(m, monkeypatch, env):
    script = dict(_VIDEO_SCRIPT, cards=[{"title": "Growth 🚀", "body": "Plain body."}])
    be = FakeBackend(script_states=[{"status": "accepted", "script": script}])
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "unrenderable_text"
    assert be.posts_bodies == []


def test_a_card_that_cannot_fit_skips_the_day(m, monkeypatch, env):
    from marketing import cards
    from marketing import render as rd

    def overflow(**_k):
        raise cards.CardOverflow("one word wider than the zone")

    be = _video_backend()
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(rd, "produce_video", overflow)
    assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "unrenderable_text"


def test_a_script_the_judge_did_not_enforce_skips_the_day_at_the_posts(m, monkeypatch, env):
    be = FakeBackend(posts_status=409, posts_error_code="MARKETING_JUDGE_NOT_ENFORCED")
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "judge_not_enforced"


def test_any_other_posts_refusal_fails_the_run(m, monkeypatch, env):
    be = FakeBackend(posts_status=422, posts_error_code="MARKETING_REQUEST_INVALID")
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert _closing_patch(be)["status"] == "failed"


def test_a_video_outlet_without_a_verified_video_fails_loudly(m, monkeypatch, env):
    """A resume past `rendered` whose video pointer does not verify must never post the video
    outlets without their video (or silently drop them)."""
    be = _video_backend()
    be.run["stage"] = "rendered"
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert "no verified video" in _closing_patch(be)["last_error"] and be.posts_bodies == []


def test_the_post_format_map_is_one_the_server_records_for_every_composed_outlet():
    from app.api.error_response import ErrorCode
    from app.schemas.marketing import POST_FORMATS_BY_PLATFORM
    from app.services.marketing import post_copy
    from marketing import render as rd

    assert set(rd.POST_FORMAT) == set(post_copy.PLATFORMS) == set(POST_FORMATS_BY_PLATFORM)
    for platform, fmt in rd.POST_FORMAT.items():
        assert fmt in POST_FORMATS_BY_PLATFORM[platform], (platform, fmt)
    # The caption disclaimer is composed per PLATFORM, not per format: an outlet gets the narrated
    # video only if its caption already says so ("Script and narration generated with AI").
    from datetime import date

    field = {"youtube": "youtube_description"}
    for platform, fmt in rd.POST_FORMAT.items():
        text = post_copy.disclaimer_for(field.get(platform, platform), date(2026, 9, 17)) or ""
        if fmt == "video":
            assert "narration" in text, platform
        else:
            assert "narration" not in text, platform
    assert rd.JUDGE_NOT_ENFORCED == ErrorCode.MARKETING_JUDGE_NOT_ENFORCED.value


def test_post_specs_edges():
    from marketing import render as rd

    assert rd.post_specs([], None) == []
    assert rd.post_specs(["x", "pinterest"], None) == [{"platform": "x", "format": "text"}]
    with pytest.raises(rd.RenderInputError):
        rd.post_specs(["tiktok"], None)
    assert rd.post_specs(["tiktok", "tiktok"], "v")[0] == {"platform": "tiktok", "format": "video", "asset_ids": ["v"]}


# ── drop 1 (2026-10-09): the post image and the run's frozen formats (contract C5) ─────────────


_IMAGE_POST = {"title": "Mr. Market's mood is not the business",
               "paragraphs": ["Every day he names a price for your share of the business.",
                              "His price follows his mood, not the business itself."]}


def _footer() -> str:
    from datetime import date

    from app.services.marketing import post_copy

    return post_copy.image_footer(date(2026, 9, 17))


def _server_script(platforms, *, image_posts=True, x_images=False, image_post=_IMAGE_POST) -> Dict[str, Any]:
    """The worker script EXACTLY as the server builds it: the writer's package frozen at write time
    (script_service.freeze_post_formats) and cut down for the worker (script_service.worker_script),
    validated against the wire schema the endpoint answers with."""
    from datetime import date

    from app.schemas.marketing import WorkerScript
    from app.services.marketing import script_service

    package = {k: v for k, v in _VIDEO_SCRIPT.items() if k != "outlets"}
    package["posts"] = {p: {"caption": f"server {p} copy"} for p in platforms}
    package["image_post"] = image_post
    output = script_service.freeze_post_formats(package, date(2026, 9, 17), image_posts=image_posts,
                                                x_images=x_images, run_id="run-1")
    return WorkerScript.model_validate(script_service.worker_script(output)).model_dump()


def _accepted(script: Dict[str, Any]) -> FakeBackend:
    return FakeBackend(script_states=[{"status": "accepted", "source_ref": "journey:mr_market",
                                       "template_id": "case_story", "script": script}])


def _image_script(**over) -> Dict[str, Any]:
    s = dict(_VIDEO_SCRIPT, outlets=["bluesky", "facebook", "tiktok", "x"],
             post_formats={"bluesky": "image", "facebook": "text", "tiktok": "video", "x": "image"},
             image_post=_IMAGE_POST, image_footer=_footer())
    s.update(over)
    return s


def _cards(be: FakeBackend) -> List[Dict[str, Any]]:
    return [a for a in be.assets.values() if a["kind"] == "card"]


def test_an_image_and_video_day_records_each_outlet_in_its_frozen_format(m, monkeypatch, env):
    pytest.importorskip("PIL")
    from app.schemas.marketing import AssetRegisterRequest, normalize_image_post
    from marketing import cards

    script = _server_script(["bluesky", "facebook", "instagram", "linkedin", "threads", "tiktok", "x", "youtube"])
    assert script["post_formats"] == {"bluesky": "image", "facebook": "image", "instagram": "video",
                                      "linkedin": "image", "threads": "image", "tiktok": "video",
                                      "x": "text", "youtube": "video"}       # X images off by default
    be = _accepted(script)
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    (card,) = _cards(be)
    (video,) = [a for a in be.assets.values() if a["kind"] == "video"]
    md = card["metadata"]
    footer = script["image_footer"]
    assert md["onscreen_text"] == [_IMAGE_POST["title"], *_IMAGE_POST["paragraphs"], footer]
    assert md["image_role"] == cards.IMAGE_ROLE_POST == "post_image" and len(md["render_key"]) == 64
    assert md["card_version"] == cards.CARD_RENDER_VERSION and md["template_id"] == "case_story"
    assert card["content_type"] == "image/jpeg" and card["status"] == "ready"
    # What went over the wire passes the server's own request schema and its allow-list.
    reg = next(b for b in be.registered if b["kind"] == "card")
    AssetRegisterRequest.model_validate(reg)
    assert reg["ext"] == "jpg" and 0 < reg["bytes"] <= cards.POST_IMAGE_MAX_BYTES
    stored = normalize_image_post(_IMAGE_POST)
    assert set(md["onscreen_text"]) <= {stored["title"], *stored["paragraphs"], footer} and footer in md["onscreen_text"]
    (body,) = [b for p, b in be.uploaded.items() if "/card-" in p]
    assert b"Content-Type: image/jpeg" in body and b"\xff\xd8\xff" in body       # a real JPEG went up
    # The image is registered BEFORE the video, and both pointers ride in the ONE checkpoint PATCH.
    kinds = [b["kind"] for b in be.registered]
    assert kinds.index("card") < kinds.index("video")
    rendered = next(p for p in be.patches if p.get("stage") == "rendered")
    assert rendered["metadata"] == {"image_asset_id": card["id"], "video_asset_id": video["id"]}
    ((posts,),) = [list(b.values()) for b in be.posts_bodies]
    by_platform = {p["platform"]: p for p in posts}
    assert {p: s["format"] for p, s in by_platform.items()} == script["post_formats"]
    for p, spec in by_platform.items():
        assert spec.get("asset_ids") == {"image": [card["id"]], "video": [video["id"]]}.get(spec["format"]), p
    assert be.run["status"] == "media_ready"


def test_an_image_only_day_draws_the_image_and_never_narrates(m, monkeypatch, env):
    pytest.importorskip("PIL")
    from marketing import voice as vc

    def never(lines, **k):
        raise AssertionError("an image-only day must never be narrated")

    script = _server_script(["bluesky", "x"], x_images=True)
    assert script["post_formats"] == {"bluesky": "image", "x": "image"}
    be = _accepted(script)
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(vc, "run_child", never)
    assert m.main() == 0
    (card,) = _cards(be)
    assert not [a for a in be.assets.values() if a["kind"] in ("audio", "video")]
    voiced = next(p for p in be.patches if p.get("stage") == "voiced")
    rendered = next(p for p in be.patches if p.get("stage") == "rendered")
    assert "metadata" not in voiced and rendered["metadata"] == {"image_asset_id": card["id"]}
    assert be.posts_bodies == [{"posts": [{"platform": "bluesky", "format": "image", "asset_ids": [card["id"]]},
                                          {"platform": "x", "format": "image", "asset_ids": [card["id"]]}]}]
    # The render is a real stage on such a day (render + upload): it keeps the full start margin,
    # while the voice stage, with nothing to narrate, takes the quick one.
    assert m.stage_start_margin("rendered", {"script": script}) == m.STAGE_START_MARGIN_SECONDS
    assert m.stage_start_margin("voiced", {"script": script}) == m.QUICK_STAGE_MARGIN_SECONDS


def test_image_switches_off_records_text_posts_and_draws_no_image(m, monkeypatch, env):
    script = _server_script(["bluesky", "tiktok", "x"], image_posts=False)
    assert script["post_formats"] == {"bluesky": "text", "tiktok": "video", "x": "text"}
    # The server still hands over the stored image_post (no footer): the FORMATS decide, never its
    # mere presence — an image nobody posts would be an unchecked public object.
    assert script["image_post"] == _IMAGE_POST and script["image_footer"] is None
    be = _accepted(script)
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert _cards(be) == []
    rendered = next(p for p in be.patches if p.get("stage") == "rendered")
    assert set(rendered["metadata"]) == {"video_asset_id"}
    assert [(p["platform"], p["format"]) for p in be.posts_bodies[0]["posts"]] == [
        ("bluesky", "text"), ("tiktok", "video"), ("x", "text")]


def test_a_matching_ready_post_image_is_reused_without_drawing_again(m, monkeypatch, env):
    pytest.importorskip("PIL")
    from marketing import cards
    from marketing import render as rd

    be = _accepted(_image_script())
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    (card,) = _cards(be)

    def boom(*a, **k):
        raise AssertionError("a reused render must not be drawn again")

    monkeypatch.setattr(cards, "render_image", boom)
    monkeypatch.setattr(rd, "produce_video", boom)
    # a later tick re-claims the SAME run after a lost close (resume from `voiced`)
    be.run.update({"status": "in_progress", "stage": "voiced"})
    for key in ("image_asset_id", "video_asset_id"):
        be.run["metadata"].pop(key)
    n_registered = len(be.registered)
    assert m.main() == 0
    assert _cards(be) == [card] and be.run["metadata"]["image_asset_id"] == card["id"]
    assert not [b for b in be.registered[n_registered:] if b["kind"] in ("card", "video")]
    assert be.run["status"] == "media_ready"


@pytest.mark.parametrize("over", [
    {"image_post": {"title": "Growth 🚀", "paragraphs": ["Plain one.", "Plain two."]}},
    {"cards": [{"title": "Growth 🚀", "body": "Plain body."}]},       # the VIDEO's card: no image left behind
])
def test_a_glyph_the_font_cannot_draw_skips_the_day_before_anything_is_uploaded(m, monkeypatch, env, over):
    pytest.importorskip("PIL")
    be = _accepted(_image_script(**over))
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "unrenderable_text"
    assert not [b for b in be.registered if b["kind"] in ("card", "video")] and be.posts_bodies == []


def test_a_video_card_that_cannot_fit_leaves_no_post_image_behind(m, monkeypatch, env):
    pytest.importorskip("PIL")
    from marketing import cards
    from marketing import render as rd

    def overflow(**_k):
        raise cards.CardOverflow("one word wider than the zone")

    be = _accepted(_image_script())
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(rd, "produce_video", overflow)
    assert m.main() == 0
    assert be.run["metadata"]["skip_reason"] == "unrenderable_text"
    assert _cards(be) == [] and be.posts_bodies == []


def test_image_text_that_cannot_fit_whole_skips_the_day(m, monkeypatch, env):
    pytest.importorskip("PIL")
    long = " ".join(["Diversification"] * 37)[:600]
    be = _accepted(_image_script(image_post={"title": "t", "paragraphs": [long] * 4}))
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert be.run["metadata"]["skip_reason"] == "unrenderable_text"
    assert not [b for b in be.registered if b["kind"] in ("card", "video")]


def test_an_image_over_the_cap_at_every_quality_skips_the_day_and_is_never_sent(m, monkeypatch, env):
    pytest.importorskip("PIL")
    from marketing import cards

    be = _accepted(_image_script())
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(cards, "POST_IMAGE_MAX_BYTES", 1000)
    assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "image_too_large"
    assert not [b for b in be.registered if b["kind"] in ("card", "video")]


def test_the_byte_assert_refuses_an_oversize_image_even_if_the_ladder_let_it_through(m, monkeypatch, env):
    pytest.importorskip("PIL")
    from marketing import cards

    real = cards.render_image

    def lying(*a, **k):
        out = real(*a, **k)
        return cards.RenderedImage(data=out.data + b"\0" * cards.POST_IMAGE_MAX_BYTES, quality=out.quality,
                                   layout=out.layout)

    be = _accepted(_image_script())
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(cards, "render_image", lying)
    assert m.main() == 1
    assert "over" in _closing_patch(be)["last_error"] and _cards(be) == []


@pytest.mark.parametrize("over, needle", [
    ({"image_post": None}, "no image_post"),
    ({"image_footer": None}, "image_footer"),
    ({"image_post": {"title": "t", "paragraphs": ["only one"]}}, "paragraphs"),
    ({"post_formats": {"x": "carousel"}}, "post_formats"),
    ({"post_formats": ["x", "image"]}, "post_formats"),
])
def test_an_image_contract_breach_fails_the_run_loudly(m, monkeypatch, env, over, needle):
    be = _accepted(_image_script(**over))
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    closing = _closing_patch(be)
    assert closing["status"] == "failed" and needle in closing["last_error"], closing
    assert be.posts_bodies == []


def test_a_resume_past_rendered_without_a_verified_post_image_fails_loudly(m, monkeypatch, env):
    """Never post an image outlet without its image (or silently as text)."""
    be = _accepted(_image_script(post_formats={"bluesky": "image", "facebook": "text", "tiktok": "text",
                                               "x": "text"}))
    be.run["stage"] = "rendered"
    be.run["metadata"] = {"image_asset_id": "asset-not-there"}
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert "no verified post image" in _closing_patch(be)["last_error"] and be.posts_bodies == []


def test_frozen_and_outlet_formats():
    from marketing import render as rd

    old = {"outlets": ["tiktok", "x", "pinterest"]}
    assert rd.frozen_formats(old) is None and rd.frozen_formats({"post_formats": None}) is None
    assert rd.outlet_formats(old) == {"tiktok": "video", "x": "text"}           # POST_FORMAT fallback
    assert rd.video_needed(old) and not rd.image_needed(old)
    new = {"outlets": ["x", "bluesky", "tiktok"], "post_formats": {"x": "image", "bluesky": "text"}}
    assert rd.outlet_formats(new) == {"x": "image", "bluesky": "text"}         # the frozen map wins
    assert rd.image_needed(new) and not rd.video_needed(new)
    assert rd.frozen_formats(new) == new["post_formats"] and rd.frozen_formats(new) is not new["post_formats"]
    assert rd.outlet_formats({"outlets": None}) == {} and rd.outlet_formats({}) == {}
    for bad in ("image", ["x"], {"x": "carousel"}, {"x": None}, {"x": 1}, {3: "text"}):
        with pytest.raises(rd.RenderInputError):
            rd.frozen_formats({"post_formats": bad})
        with pytest.raises(rd.RenderInputError):
            rd.outlet_formats({"outlets": ["x"], "post_formats": bad})


def test_post_specs_follow_the_frozen_formats(caplog):
    from marketing import render as rd

    formats = {"bluesky": "image", "facebook": "text", "tiktok": "video"}
    assert rd.post_specs(["bluesky", "facebook", "tiktok"], "v", "i", formats=formats) == [
        {"platform": "bluesky", "format": "image", "asset_ids": ["i"]},
        {"platform": "facebook", "format": "text"},
        {"platform": "tiktok", "format": "video", "asset_ids": ["v"]}]
    with pytest.raises(rd.RenderInputError, match="post image"):
        rd.post_specs(["bluesky"], "v", None, formats=formats)
    with pytest.raises(rd.RenderInputError, match="video"):
        rd.post_specs(["tiktok"], None, "i", formats=formats)
    with pytest.raises(rd.RenderInputError, match="frozen format"):
        rd.post_specs(["x"], None, None, formats={"x": "carousel"})
    # An outlet with copy but no frozen format: no post, logged at ERROR (never a guess the server
    # would refuse — which would fail every other post of the day with it).
    caplog.set_level(logging.ERROR, logger="marketing.render")
    assert rd.post_specs(["x", "facebook"], None, None, formats={"facebook": "text"}) == [
        {"platform": "facebook", "format": "text"}]
    assert any("'x'" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)
    # The image id is never used for a text or video post, nor the video id for an image post.
    assert rd.post_specs(["facebook"], "v", "i", formats=formats) == [{"platform": "facebook", "format": "text"}]


def test_the_worker_mirrors_the_servers_image_contract():
    from app.schemas import marketing as sch
    from marketing import render as rd
    from marketing import video

    assert rd.FROZEN_FORMATS == sch.FROZEN_POST_FORMATS
    assert sch.POST_MEDIA_KINDS["image"] == ("card",)
    # Every format the server can freeze for a platform is one the worker records.
    for platform, allowed in sch.POST_FORMATS_BY_PLATFORM.items():
        assert set(allowed) <= set(rd.FROZEN_FORMATS), platform
    assert rd.RENDER_STAGE_VERSION == "render/v3"      # v3: drop 2a (template scripts)
    from marketing import logos

    assert rd.worst_case_seconds(120.0) == (video.worst_case_seconds(120.0) + rd.IMAGE_RENDER_SECONDS + 120.0
                                            + logos.LOGOS_WORST_CASE_SECONDS)


def test_the_image_render_key_changes_with_every_input_that_changes_the_bytes():
    from marketing import render as rd

    base = dict(texts=["T", "P1", "P2", "F"], card_version="c1", layout_engine="raqm", max_bytes=950_000)
    k = rd.image_render_key(**base)
    assert k == rd.image_render_key(**base) and len(k) == 64
    for change in ({"texts": ["T", "P1", "P2", "F!"]}, {"texts": ["T", "P2", "P1", "F"]}, {"card_version": "c2"},
                   {"layout_engine": "basic"}, {"max_bytes": 900_000}):
        assert rd.image_render_key(**{**base, **change}) != k, change
    # never the video's key for the same strings
    assert k != rd.render_key(audio_sha256="a" * 64, words=[], card_texts=[base["texts"]], threads=2,
                              card_version="c1", video_version="v1", max_seconds=75.0, layout_engine="raqm")


def test_the_render_readiness_check_names_every_missing_piece(m, monkeypatch, tmp_path):
    out = m.render_readiness(str(tmp_path / "fonts"))
    assert not out["ready"]
    assert any("Inter-Bold" in p for p in out["problems"]) and any("logo" in p for p in out["problems"])
    fonts = _PKG / "assets" / "fonts"
    monkeypatch.setattr(m.shutil, "which", lambda name: None)
    out = m.render_readiness(str(fonts))
    assert "ffmpeg/ffprobe missing" in out["problems"]
    assert not any("logo" in p or "Inter" in p for p in out["problems"])   # the vendored assets exist


def test_the_render_threads_follow_the_override_then_the_cgroup(monkeypatch):
    from marketing import render as rd

    monkeypatch.setenv("MARKETING_RENDER_THREADS", "3")
    assert rd.render_threads() == 3
    monkeypatch.setenv("MARKETING_RENDER_THREADS", "64")
    assert rd.render_threads() == rd.RENDER_THREADS_MAX
    monkeypatch.setenv("MARKETING_RENDER_THREADS", "zero")
    assert 1 <= rd.render_threads() <= rd.RENDER_THREADS_MAX


def test_the_render_key_changes_with_every_input_that_changes_the_bytes():
    from marketing import render as rd

    base = dict(audio_sha256="a" * 64, words=[{"w": "hi", "s": 0.0, "e": 0.4, "line": 0}],
                card_texts=[["Caydex"], ["T", "B"]], threads=2, card_version="c1", video_version="v1",
                max_seconds=75.0, layout_engine="raqm")
    k = rd.render_key(**base)
    assert k == rd.render_key(**base) and len(k) == 64
    for change in ({"audio_sha256": "b" * 64}, {"threads": 3}, {"card_version": "c2"},
                   {"video_version": "v2"}, {"card_texts": [["Caydex"], ["T", "B!"]]},
                   {"words": [{"w": "hi", "s": 0.0, "e": 0.5, "line": 0}]}, {"max_seconds": 60.0},
                   {"layout_engine": "basic"}):
        assert rd.render_key(**{**base, **change}) != k, change


# ── no social secret reaches the worker (Phase 5, rules/marketing.md §2) ──────────────────────
# The media worker holds NO social secret: only the publisher (web process) calls a platform and
# only the web process runs the Telegram bot. A worker that NAMES one of those settings has been
# given (or is about to be given) a credential it must never hold.

_SOCIAL_SETTING = re.compile(r"MARKETING_(?:X|BLUESKY|UPLOAD_POST|TELEGRAM)_[A-Z0-9_]*")
#: A setting name built at run time (`f"MARKETING_{p}_TOKEN"`, `"MARKETING_" + p`) cannot be
#: checked, so it fails closed. `"MARKETING_"` on its own (a prefix test) is fine: it reads the
#: worker's own environment, which holds none of these.
_DYNAMIC_PREFIX = "MARKETING_"


def _docstring_ids(tree: ast.AST) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def social_setting_names(src: str) -> List[tuple]:
    """(line, name) for every social-outlet setting the CODE of one worker file names: string
    literals (f-string parts and bytes included) and identifiers (names, attributes, keywords,
    arguments, definitions, import aliases). Comments are not code — the AST drops them — and a
    docstring is prose, so the explanatory text next to a rule can neither trip nor satisfy it."""
    tree = ast.parse(src)
    docs = _docstring_ids(tree)
    found: List[tuple] = []
    for node in ast.walk(tree):
        texts: List[str] = []
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)) and id(node) not in docs:
            texts.append(node.value.decode("latin-1") if isinstance(node.value, bytes) else node.value)
        elif isinstance(node, ast.Name):
            texts.append(node.id)
        elif isinstance(node, ast.Attribute):
            texts.append(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            texts.append(node.arg)
        elif isinstance(node, ast.arg):
            texts.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            texts.append(node.name)
        elif isinstance(node, ast.alias):
            texts.append(node.asname or node.name)
        elif isinstance(node, ast.JoinedStr):
            head = node.values[0] if node.values else None
            if (isinstance(head, ast.Constant) and isinstance(head.value, str)
                    and head.value.endswith(_DYNAMIC_PREFIX) and len(node.values) > 1):
                found.append((node.lineno, f"{head.value}<dynamic>"))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            left = node.left
            if (isinstance(left, ast.Constant) and isinstance(left.value, str)
                    and (left.value.endswith(_DYNAMIC_PREFIX) or left.value.startswith(_DYNAMIC_PREFIX + "%"))):
                found.append((node.lineno, f"{left.value}<dynamic>"))
        for text in texts:
            found.extend((getattr(node, "lineno", 0), m.group(0)) for m in _SOCIAL_SETTING.finditer(text))
    return found


def _non_comment_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _social_hits(root: Path = _PKG) -> List[str]:
    hits = [f"{py.relative_to(root)}:{line}: {name}" for py in _worker_files(root)
            for line, name in social_setting_names(py.read_text(encoding="utf-8"))]
    for cfg in ("Dockerfile", "railway.toml"):   # an ENV / variables line would hand it over too
        path = root / cfg
        if path.exists():
            hits += [f"{cfg}: {m.group(0)}" for m in _SOCIAL_SETTING.finditer(_non_comment_lines(path.read_text()))]
    return hits


def test_no_worker_file_names_a_social_outlet_setting():
    files = _worker_files()
    assert _SCRIPT in files and len(files) >= 5, files
    assert _social_hits() == [], "the media worker must hold no X / Bluesky / Upload-Post / Telegram secret"
    # Anti-vacuity: the same scan DOES see the worker's own settings in the code it reads.
    general = re.compile(r"MARKETING_[A-Z0-9_]+")
    seen = {m.group(0) for node in ast.walk(ast.parse(_SCRIPT.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            for m in general.finditer(node.value)}
    assert {"MARKETING_WORKER_TOKEN", "MARKETING_API_BASE_URL"} <= seen, seen


def test_the_family_covers_every_social_setting_the_web_declares():
    """A new outlet's settings must fall in the guarded families, or the guard silently misses
    them (this is what to extend when Upload-Post or another outlet lands)."""
    from app.config import Settings

    social = [n for n in Settings.model_fields
              if n.startswith("MARKETING_") and any(k in n for k in ("_X_", "BLUESKY", "UPLOAD_POST", "TELEGRAM"))]
    assert {"MARKETING_X_ACCESS_TOKEN", "MARKETING_X_CONSUMER_SECRET", "MARKETING_BLUESKY_APP_PASSWORD",
            "MARKETING_TELEGRAM_BOT_TOKEN"} <= set(social), social
    assert all(_SOCIAL_SETTING.fullmatch(n) for n in social), social


@pytest.mark.parametrize("src", [
    "import os\ntoken = os.environ['MARKETING_X_ACCESS_TOKEN']\n",
    "import os\ndef f():\n    return os.getenv('MARKETING_BLUESKY_APP_PASSWORD', '')\n",
    "KEY = f\"MARKETING_TELEGRAM_BOT_TOKEN={1}\"\n",
    "x = b'MARKETING_UPLOAD_POST_API_KEY'\n",
    "MARKETING_X_CONSUMER_KEY = None\n",
    "def run(*, MARKETING_TELEGRAM_REVIEW_CHAT_ID=0):\n    pass\n",
    "settings.MARKETING_X_ACCESS_TOKEN_SECRET\n",
    "import os\nname = f'MARKETING_{platform}_TOKEN'\n",
    "import os\nname = 'MARKETING_' + platform.upper() + '_TOKEN'\n",
    "import os\nname = 'MARKETING_%s_TOKEN' % platform\n",
])
def test_the_social_setting_scanner_flags(src):
    assert social_setting_names(src), src


@pytest.mark.parametrize("src", [
    "# reads MARKETING_X_ACCESS_TOKEN? never — the publisher does\nx = 1\n",
    '"""The worker never holds MARKETING_TELEGRAM_BOT_TOKEN."""\nx = 1\n',
    'def f():\n    """No MARKETING_BLUESKY_APP_PASSWORD here."""\n    return 1\n',
    "import os\nt = os.environ.get('MARKETING_WORKER_TOKEN')\n",
    "import os\nmine = {k for k in os.environ if k.startswith('MARKETING_')}\n",
    "x = 'MARKETING_XRAY_MODE'\n",   # not the X family: no underscore after X
])
def test_the_social_setting_scanner_passes(src):
    assert social_setting_names(src) == [], src


def test_a_social_setting_in_a_copy_of_the_real_tree_is_caught(tmp_path):
    """Mutation check on a COPY of what ships (never the real tree): the real-tree guard must
    turn red the moment a worker file reads one of these settings, in a stage function or in the
    Dockerfile, and a comment saying the same thing must not."""
    copy = tmp_path / "marketing"
    shutil.copytree(_PKG, copy, ignore=_copy_ignore(_PKG))   # what `COPY . marketing/` ships, Dockerfile included
    assert (copy / "Dockerfile").exists() and _social_hits(copy) == []
    main = copy / "main.py"
    clean = main.read_text(encoding="utf-8")
    main.write_text(clean + "\n# MARKETING_X_ACCESS_TOKEN lives on the web service only\n", encoding="utf-8")
    assert _social_hits(copy) == []
    main.write_text(clean + "\n\ndef _leak():\n    return os.environ.get(\"MARKETING_TELEGRAM_BOT_TOKEN\")\n",
                    encoding="utf-8")
    assert [h for h in _social_hits(copy) if "MARKETING_TELEGRAM_BOT_TOKEN" in h and h.startswith("main.py:")]
    main.write_text(clean, encoding="utf-8")
    voice = copy / "voice.py"
    voice.write_text(voice.read_text(encoding="utf-8") + "\nKEY = 'MARKETING_BLUESKY_APP_PASSWORD'\n",
                     encoding="utf-8")
    assert any(h.startswith("voice.py:") for h in _social_hits(copy))
    voice.write_text((_PKG / "voice.py").read_text(encoding="utf-8"), encoding="utf-8")
    docker = copy / "Dockerfile"
    docker.write_text(docker.read_text() + "\nENV MARKETING_X_CONSUMER_SECRET=\"\"\n")
    assert _social_hits(copy) == ["Dockerfile: MARKETING_X_CONSUMER_SECRET"]


# ── drop 1 (compat F2): the worker declares what it can render on every claim ──


def test_every_claim_declares_the_post_image_capability_the_server_accepts(m, monkeypatch, env, caplog):
    """The server freezes `image` post formats only for a run whose claiming worker declared it can
    render the post image; this worker does (render.py draws the 4:5 post image), on every claim — and
    the server's claim schema keeps exactly what it sends (nothing dropped as unknown). Drop 2a: it also
    declares `news_templates` (it draws the per-line template video, its opening card and the
    `image_spec` image) — without it the server gives the run the lesson, never a company-news day. Drop
    2b: `layouts_2b` (it draws the `pair` / `grid` images) — without it the server drops company_stakes
    and theme_explainer from the day's chain."""
    import logging

    from app.schemas.marketing import WORKER_CAPABILITIES, RunClaimRequest

    assert m.BackendClient.WORKER_CAPABILITIES == WORKER_CAPABILITIES == ("post_image", "news_templates",
                                                                          "layouts_2b")
    be = FakeBackend()
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    (body,) = be.claim_bodies
    assert body["capabilities"] == ["post_image", "news_templates", "layouts_2b"]
    with caplog.at_level(logging.WARNING):
        assert RunClaimRequest.model_validate(body).capabilities == ["layouts_2b", "news_templates", "post_image"]
    assert not [r for r in caplog.records if "does not know" in r.getMessage()]


def test_the_worker_declares_layouts_2b_only_because_its_cards_draw_every_2b_layout(m):
    """`layouts_2b` is a promise about THIS image's renderer: the server hands a `pair` / `grid` image only
    to a holder that declared it, so the worker's own `cards.SHIPPED_LAYOUTS` must list every one of
    WORKER_LAYOUTS_2B (a worker that declared it without drawing them would fail those days)."""
    from app.schemas.marketing import WORKER_CAPABILITY_LAYOUTS_2B, WORKER_LAYOUTS_2B
    from marketing import cards

    assert WORKER_CAPABILITY_LAYOUTS_2B in m.BackendClient.WORKER_CAPABILITIES
    assert set(WORKER_LAYOUTS_2B) <= set(cards.SHIPPED_LAYOUTS), (WORKER_LAYOUTS_2B, cards.SHIPPED_LAYOUTS)


def test_the_preview_docs_name_only_scripts_and_flags_that_exist():
    """Review R9 (low): marketing/preview.py told the operator to write a template preview with
    `scripts/marketing_preview.py --series`, which has no such flag (the generator is
    scripts/marketing_news_preview.py). Every `scripts/<name>.py --flag …` the module names must be a
    real file defining each flag named right after it; the generator it names mirrors its bucket host."""
    preview = (_PKG / "preview.py").read_text(encoding="utf-8")
    scripts_dir = _PKG.parent / "scripts"
    refs = re.findall(r"scripts/(\w+\.py)((?:[ \t]+--[a-z][a-z-]*)*)", preview)
    assert refs and any(name == "marketing_news_preview.py" for name, _ in refs), refs
    for name, flags in refs:
        target = scripts_dir / name
        assert target.is_file(), name
        text = target.read_text(encoding="utf-8")
        for flag in re.findall(r"--[a-z][a-z-]*", flags):
            assert re.search(r"add_argument\(\s*[\"']" + re.escape(flag) + r"[\"']", text), (name, flag)
    generator = (scripts_dir / "marketing_news_preview.py").read_text(encoding="utf-8")
    assert '"marketing.preview"' in generator and "PREVIEW_BUCKET_HOST" in generator


# ── drop 2a (contract D13/D14): template (news) days — logos, the per-line video, the news image ──


_TPL_HOST = "sb.example"
_LOGO_BASE = f"https://{_TPL_HOST}/storage/v1/object/public/marketing-media/logos/"


def _real_download():
    """`render.download` itself — the autouse `_no_real_voice` fixture replaces the module attribute
    with a fake for every test, so the original is taken from a pristine copy of the module."""
    spec = importlib.util.spec_from_file_location("marketing_render_pristine", _PKG / "render.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _png_bytes(size=(200, 200), colour=(220, 20, 20, 255), *, fmt="PNG", mode="RGBA") -> bytes:
    from PIL import Image

    im = Image.new(mode, size, (0, 0, 0, 0) if mode == "RGBA" else 0)
    if mode == "RGBA":
        im.paste(colour, (20, 20, size[0] - 20, size[1] - 20))
    buf = __import__("io").BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


def _logo_entry(key: str, name: str, data: bytes, ext: str = "png") -> Dict[str, Any]:
    import hashlib

    sha = hashlib.sha256(data).hexdigest()
    return {"key": key, "name": name, "url": f"{_LOGO_BASE}{sha[:32]}.{ext}", "sha256": sha, "bytes": len(data),
            "width": 200, "height": 200}


class _Downloads:
    """`render.download` for a template day: logo URLs answer from `objects` (counted), anything else
    is the narration."""

    def __init__(self, objects: Dict[str, bytes]):
        self.objects = objects
        self.calls: List[tuple] = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if "/logos/" in url:
            if url not in self.objects:
                raise RuntimeError("HTTP 404")
            return self.objects[url]
        return _FAKE_M4A


def test_logos_resolve_to_verified_files_or_wordmarks(tmp_path, caplog):
    pytest.importorskip("PIL")
    from marketing import logos as lg

    good, other = _png_bytes(), _png_bytes(colour=(20, 200, 40, 255))
    a, b = _logo_entry("AAA", "Alpha", good), _logo_entry("BBB", "Beta", good)
    tampered = _logo_entry("CCC", "Gamma", other)
    dl = _Downloads({a["url"]: good, tampered["url"]: good})        # CCC's object holds other bytes
    script = {"logos": [a, dict(a, name="Alpha again"), tampered,
                        {"key": "DDD", "name": "Delta", "url": None, "sha256": None},
                        {"key": "", "name": "no key"}, "junk"]}
    caplog.set_level(logging.INFO, logger="marketing.logos")
    out = lg.resolve_logos(script, bucket_origin=_TPL_HOST, download=dl, dest_dir=tmp_path)
    assert set(out) == {"AAA", "CCC", "DDD"}
    assert out["AAA"].read_bytes() == good and out["AAA"].name == f"logo-{a['sha256'][:32]}.png"
    assert out["CCC"] is None and out["DDD"] is None
    assert [u for u, _k in dl.calls] == [a["url"], tampered["url"]]          # one fetch per key; none for DDD
    _url, kw = dl.calls[0]
    assert kw["max_bytes"] == lg.LOGO_MAX_BYTES and 0 < kw["timeout"] <= lg.LOGO_DOWNLOAD_TIMEOUT_SECONDS
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "CCC" in errors[0].getMessage() and "sha256" in errors[0].getMessage()
    assert all(_LOGO_BASE not in r.getMessage() for r in caplog.records)   # no URL in a log line
    assert b  # (same bytes, different key: the address is content-only, both may share it)


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(url=e["url"].replace("https://", "http://")),
    lambda e: e.update(url=e["url"].replace(_TPL_HOST, "evil.example")),
    lambda e: e.update(url=e["url"].replace(_TPL_HOST, f"{_TPL_HOST}.evil.example")),
    lambda e: e.update(url=e["url"].replace("https://", f"https://user@")),
    lambda e: e.update(url=e["url"].replace("marketing-media", "other-bucket")),
    lambda e: e.update(url=e["url"].replace("/logos/", "/2026-11-16/")),
    lambda e: e.update(url=e["url"].replace("/logos/", "/logos/../")),
    lambda e: e.update(url=e["url"] + "?download=1"),
    lambda e: e.update(url=e["url"].replace(".png", ".gif")),
    lambda e: e.update(url=e["url"].replace(".png", ".PNG")),
    lambda e: e.update(url=e["url"].upper().replace("HTTPS://SB.EXAMPLE", "https://sb.example")),
    lambda e: e.update(url=e["url"].replace(e["sha256"][:32], "0" * 32)),           # hex ≠ sha[:32]
    lambda e: e.update(sha256=None), lambda e: e.update(sha256="A" * 64), lambda e: e.update(sha256="ab"),
    lambda e: e.update(url=5),
], ids=["http", "other_host", "suffix_host", "userinfo", "other_bucket", "other_prefix", "dotdot", "query",
        "gif", "upper_ext", "upper_hex", "hex_mismatch", "no_sha", "upper_sha", "short_sha", "not_a_str"])
def test_a_logo_url_that_is_not_ours_is_never_fetched(tmp_path, mutate):
    from marketing import logos as lg

    entry = _logo_entry("AAA", "Alpha", _png_bytes())
    mutate(entry)
    dl = _Downloads({})
    assert lg.resolve_logos({"logos": [entry]}, bucket_origin=_TPL_HOST, download=dl, dest_dir=tmp_path) == {"AAA": None}
    assert dl.calls == []


def test_no_bucket_origin_means_every_logo_is_a_wordmark_and_nothing_is_fetched(tmp_path):
    from marketing import logos as lg

    dl = _Downloads({})
    entry = _logo_entry("AAA", "Alpha", _png_bytes())
    assert lg.resolve_logos({"logos": [entry]}, bucket_origin=None, download=dl, dest_dir=tmp_path) == {"AAA": None}
    assert dl.calls == []
    assert lg.resolve_logos({}, bucket_origin=_TPL_HOST, download=dl, dest_dir=tmp_path) == {}
    assert lg.resolve_logos({"logos": "AAA"}, bucket_origin=_TPL_HOST, download=dl, dest_dir=tmp_path) == {}


def test_the_bucket_origin_comes_from_the_verified_narration_then_any_ready_asset():
    from marketing import logos as lg

    def asset(i, url):
        return {"id": i, "public_url": url}

    good = f"https://{_TPL_HOST}/storage/v1/object/public/marketing-media/2026-11-16/audio-1.m4a"
    other = "https://cdn.example/storage/v1/object/public/marketing-media/2026-11-16/manifest-1.json"
    assert lg.bucket_origin({"voice_asset_id": "v", "assets": [asset("m", other), asset("v", good)]}) == _TPL_HOST
    assert lg.bucket_origin({"voice_asset_id": None, "assets": [asset("m", other)]}) == "cdn.example"
    for bad in ("http://sb.example/storage/v1/object/public/marketing-media/x.json",
                "https://sb.example/object/public/marketing-media/x.json",               # not the Storage path
                "https://a@sb.example/storage/v1/object/public/marketing-media/x.json",
                "https://sb.example/storage/v1/object/public/other/x.json", None, 7):
        assert lg.bucket_origin({"assets": [asset("m", bad)]}) is None, bad
    assert lg.bucket_origin({"assets": []}) is None and lg.bucket_origin("junk") is None


@pytest.mark.parametrize("data, ext, needle", [
    ("bomb", "png", "pixels"),
    ("huge", "png", "pixels"),
    ("small", "png", "outside"),
    ("wide", "png", "aspect"),
    ("apng", "png", "animated"),
    ("jpeg_as_png", "png", "JPEG"),
    ("truncated", "png", ""),
    ("garbage", "jpg", ""),
], ids=lambda v: v if isinstance(v, str) else "")
def test_a_logo_that_does_not_decode_safely_is_a_wordmark(data, ext, needle, tmp_path, caplog):
    pytest.importorskip("PIL")
    from PIL import Image

    from marketing import logos as lg

    if data == "bomb":            # 20000×20000 bilevel: a few KB on disk, 400 M pixels decoded
        buf = __import__("io").BytesIO()
        Image.new("1", (20000, 20000)).save(buf, format="PNG")
        raw = buf.getvalue()
        assert len(raw) < lg.LOGO_MAX_BYTES
    elif data == "huge":          # past MAX_IMAGE_PIXELS, under Pillow's own bomb limit
        buf = __import__("io").BytesIO()
        Image.new("1", (3000, 3000)).save(buf, format="PNG")
        raw = buf.getvalue()
    elif data == "small":
        raw = _png_bytes(size=(60, 60))
    elif data == "wide":
        raw = _png_bytes(size=(1000, 200))
    elif data == "apng":
        buf = __import__("io").BytesIO()
        frames = [Image.new("RGB", (200, 200), c) for c in ((255, 0, 0), (0, 0, 255))]
        frames[0].save(buf, format="PNG", save_all=True, append_images=frames[1:])
        raw = buf.getvalue()
    elif data == "jpeg_as_png":
        raw = _png_bytes(fmt="JPEG", mode="RGB")
    elif data == "truncated":
        raw = _png_bytes()[:300]
    else:
        raw = b"\xff\xd8\xff\xe0" + b"\x00" * 500
    problem = lg.decode_problem(raw, ext)
    assert problem and needle in problem, problem
    entry = _logo_entry("AAA", "Alpha", raw, ext)
    caplog.set_level(logging.WARNING, logger="marketing.logos")
    out = lg.resolve_logos({"logos": [entry]}, bucket_origin=_TPL_HOST, download=_Downloads({entry["url"]: raw}),
                           dest_dir=tmp_path)
    assert out == {"AAA": None} and not list(tmp_path.iterdir())
    assert any(r.levelno == logging.WARNING and "AAA" in r.getMessage() for r in caplog.records)


def test_a_good_jpeg_and_png_decode():
    pytest.importorskip("PIL")
    from marketing import logos as lg

    assert lg.decode_problem(_png_bytes(), "png") is None
    assert lg.decode_problem(_png_bytes(fmt="JPEG", mode="RGB"), "jpg") is None
    assert lg.decode_problem(_png_bytes(), "gif") is not None


def test_an_oversize_or_failing_download_is_a_wordmark_never_a_raise(tmp_path):
    from marketing import logos as lg

    big = _logo_entry("BIG", "Big", b"x" * (lg.LOGO_MAX_BYTES + 1))
    boom = _logo_entry("ERR", "Err", _png_bytes(size=(201, 201)))

    def download(url, **kw):
        if "x" * 3 and url == boom["url"]:
            raise TimeoutError("read timed out")
        return b"x" * (lg.LOGO_MAX_BYTES + 1)

    out = lg.resolve_logos({"logos": [big, boom]}, bucket_origin=_TPL_HOST, download=download, dest_dir=tmp_path)
    assert out == {"BIG": None, "ERR": None}


def test_the_logo_downloads_share_one_bounded_budget(tmp_path, caplog):
    """12 logos on a slow host. httpx bounds each PHASE of a request separately (connect, TLS, write,
    every read), so a download is given a wall-clock SHARE (`max_seconds`, at most half of what is left)
    and each phase a quarter of it (`timeout`) — review W2: with `timeout == max_seconds` one download
    could take connect + TLS + write + header read + a chunk ≈ 5 × its share. Here every download takes
    the worst it can (`worst_case_download_seconds`: its share + one read past it) and still ENDS before
    the budget does; a logo that would start with less than MIN_DOWNLOAD_SECONDS left is a wordmark."""
    from marketing import logos as lg

    data = _png_bytes()
    entries = [_logo_entry(f"K{i}", f"Company {i}", data) for i in range(14)]
    clock = {"t": 0.0}
    calls: List[Dict[str, float]] = []

    def slow(url, **kw):
        share, timeout = kw["max_seconds"], kw["timeout"]
        calls.append({"start": clock["t"], "share": share, "timeout": timeout})
        assert timeout == share / lg.DOWNLOAD_PHASES == share / 4      # each httpx phase: a quarter
        # the first is quick; every other one takes the worst a bounded download can: its four
        # pre-body phases at `timeout` each (= its share), then one read past the elapsed check
        clock["t"] += 3.75 if len(calls) == 1 else 4 * timeout + timeout
        return data

    caplog.set_level(logging.WARNING, logger="marketing.logos")
    out = lg.resolve_logos({"logos": entries}, bucket_origin=_TPL_HOST, download=slow, dest_dir=tmp_path,
                           monotonic=lambda: clock["t"])
    assert clock["t"] <= lg.LOGOS_BUDGET_SECONDS
    for c in calls:
        left = lg.LOGOS_BUDGET_SECONDS - c["start"]
        assert left >= lg.MIN_DOWNLOAD_SECONDS
        assert c["share"] == min(lg.LOGO_DOWNLOAD_TIMEOUT_SECONDS, left / 2)
        assert c["start"] + lg.worst_case_download_seconds(c["share"]) <= lg.LOGOS_BUDGET_SECONDS
    assert [c["share"] for c in calls][-2:] == [3.125, 1.171875]       # half of the 6.25 s, 2.34375 s left
    assert len(calls) <= lg.MAX_LOGOS
    assert sum(1 for p in out.values() if p) == len(calls) and len(out) == 14
    assert any("budget" in r.getMessage() or "more than" in r.getMessage() for r in caplog.records)


def test_the_render_download_refuses_redirects_oversize_and_slow_drips(monkeypatch):
    import itertools
    import time as _time

    rd = _real_download()
    real = httpx.Client

    def serve(handler):
        monkeypatch.setattr(httpx, "Client", functools.partial(real, transport=httpx.MockTransport(handler)))

    serve(lambda req: httpx.Response(302, headers={"location": "https://evil.example/x.png"}))
    with pytest.raises(rd.RenderInputError, match="HTTP 302"):
        rd.download("https://sb.example/a.png", max_bytes=100, timeout=1)
    serve(lambda req: httpx.Response(200, content=b"x" * 101))
    with pytest.raises(rd.RenderInputError, match="exceeded 100 bytes"):
        rd.download("https://sb.example/a.png", max_bytes=100, timeout=1)
    serve(lambda req: httpx.Response(200, content=b"ok"))
    assert rd.download("https://sb.example/a.png", max_bytes=100, timeout=1) == b"ok"
    # the clock reads: the start, once the headers landed, after each chunk
    ticks = itertools.chain([0.0, 1.0], itertools.count(10.0, 10.0))   # a quick start, then a slow drip
    monkeypatch.setattr(_time, "monotonic", lambda: next(ticks))
    with pytest.raises(rd.RenderInputError, match=r"exceeded 5\.0s$"):
        rd.download("https://sb.example/a.png", max_bytes=100, timeout=1, max_seconds=5.0)
    ticks = itertools.count(0.0, 10.0)                                # a slow start: caught before the body
    monkeypatch.setattr(_time, "monotonic", lambda: next(ticks))
    with pytest.raises(rd.RenderInputError, match=r"exceeded 5\.0s before its body"):
        rd.download("https://sb.example/a.png", max_bytes=100, timeout=1, max_seconds=5.0)


def test_slow_response_headers_end_the_download_before_its_body(monkeypatch):
    """Review W2, over a real loopback socket: response headers that trickle in, each read well inside
    the per-phase `timeout`, used to be cut by nothing — `max_seconds` was checked only after a body
    chunk, so a body that then stalled surfaced as an httpx ReadTimeout one more `timeout` later. The
    elapsed check once the headers land ends it right there, as the download's own error."""
    import socket
    import threading
    import time as _time

    rd = _real_download()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    stop = threading.Event()

    def serve():
        conn, _ = server.accept()
        with conn:
            conn.settimeout(10)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            conn.sendall(b"HTTP/1.1 200 OK\r\n")
            for i in range(4):                       # 4 × 0.2 s of headers, each read inside the 1 s timeout
                if stop.wait(0.2):
                    return
                conn.sendall(f"X-Slow-{i}: v\r\n".encode())
            conn.sendall(b"Content-Length: 2\r\n\r\n")
            stop.wait(3.0)                           # the body stalls past the per-read timeout
            try:
                conn.sendall(b"ok")
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    started = _time.monotonic()
    try:
        with pytest.raises(rd.RenderInputError, match="before its body"):
            rd.download(f"http://127.0.0.1:{port}/logos/x.png", max_bytes=100, timeout=1.0, max_seconds=0.5)
        assert _time.monotonic() - started < 2.0               # ended at the headers, not after the stall
    finally:
        stop.set()
        thread.join(timeout=5)
        server.close()


def test_the_twelve_logo_worst_case_fits_the_render_margin(m):
    from marketing import logos, render, video

    worst = render.worst_case_seconds(m.UPLOAD_TIMEOUT_SECONDS)
    assert worst == (video.worst_case_seconds(m.UPLOAD_TIMEOUT_SECONDS) + render.IMAGE_RENDER_SECONDS
                     + m.UPLOAD_TIMEOUT_SECONDS + logos.LOGOS_WORST_CASE_SECONDS)
    # the logo step: the shared budget, which every download ends inside, + one per-phase read of headroom
    assert logos.LOGOS_WORST_CASE_SECONDS == (logos.MAX_LOGOS * logos.LOGO_DOWNLOAD_TIMEOUT_SECONDS
                                              + logos.LOGO_DOWNLOAD_TIMEOUT_SECONDS / logos.DOWNLOAD_PHASES)
    for tenths in range(int(logos.MIN_DOWNLOAD_SECONDS * 10), logos.LOGOS_BUDGET_SECONDS * 10 + 1):
        left = tenths / 10
        share = min(logos.LOGO_DOWNLOAD_TIMEOUT_SECONDS, left * logos.DOWNLOAD_SHARE_OF_LEFT)
        assert logos.worst_case_download_seconds(share) < left, left   # a download never outlives the budget
    assert worst <= m.STAGE_START_MARGINS["rendered"] == m.STAGE_START_MARGIN_SECONDS


def test_the_worker_maps_the_template_refusal_to_a_skipped_day(m, monkeypatch, env, caplog):
    from app.api.error_response import ErrorCode
    from marketing import render as rd

    assert rd.TEMPLATE_REFUSED == "MARKETING_TEMPLATE_REFUSED"
    server = getattr(ErrorCode, "MARKETING_TEMPLATE_REFUSED", None)
    if server is not None:                       # pinned equal once the web half (D13) declares it
        assert server.value == rd.TEMPLATE_REFUSED
    be = FakeBackend(posts_status=409, posts_error_code="MARKETING_TEMPLATE_REFUSED")
    _wire(m, monkeypatch, be)
    caplog.set_level(logging.ERROR)
    assert m.main() == 0
    assert be.run["status"] == "skipped" and be.run["metadata"]["skip_reason"] == "template_refused"
    assert any("TEMPLATE REFUSED" in r.getMessage() for r in caplog.records)
    # one create_posts call: deterministic, never retried
    assert len([c for c in be.calls if c[1].endswith("/posts")]) == 1


# ── a whole template day against the fake backend ──────────────────────────


class _TemplateBackend(FakeBackend):
    """FakeBackend whose read-back answers Storage-shaped public URLs (the shape the logos' bucket
    origin is read from)."""

    def handler(self, request):
        resp = super().handler(request)
        if request.method == "GET" and request.url.path.endswith("/assets") and resp.status_code == 200:
            body = json.loads(resp.content)
            for a in body.get("assets") or []:
                a["public_url"] = a["public_url"].replace(f"https://{_TPL_HOST}/object/public/",
                                                          f"https://{_TPL_HOST}/storage/v1/object/public/")
            return httpx.Response(200, json=body)
        return resp


def _template_world(tmp_path, *, tamper: str = ""):
    pytest.importorskip("PIL")
    from datetime import date

    from app.schemas.marketing import WorkerScript
    from app.services.marketing import post_copy

    run_date = date(2026, 9, 17)
    gme, nvda = _png_bytes(colour=(220, 20, 20, 255)), _png_bytes(colour=(20, 200, 40, 255), size=(240, 200))
    entries = [_logo_entry("GME", "GameStop", gme), _logo_entry("NVDA", "NVIDIA", nvda)]
    objects = {entries[0]["url"]: gme, entries[1]["url"]: nvda}
    if tamper:
        objects[next(e["url"] for e in entries if e["key"] == tamper)] = _png_bytes(colour=(1, 2, 3, 255))
    footer = post_copy.image_footer(run_date, "template", source="SEC Form 4 filings", as_of="Sep 17, 2026")
    opening = {"kicker": "FILED LAST WEEK · FORM 4", "logos": ["GME"], "chip": "GME", "figure": "$74.4M",
               "headline": "GameStop's CEO disclosed buying GameStop stock"}
    image_spec = {"layout": "rows", "version": 1, "kicker": "FILED LAST WEEK · FORM 4", "footer": footer,
                  "title": "CEO purchases disclosed last week",
                  "sections": [{"rows": [{"logo": "GME", "cells": ["GameStop", "CEO", "$74.4M"]},
                                         {"logo": "NVDA", "cells": ["NVIDIA", "CEO", "$12.0M"]}]}]}
    lines = ["GameStop's chief executive disclosed buying about seventy four million dollars of stock.",
             "NVIDIA's chief executive disclosed a twelve million dollar purchase the same week.",
             "Both purchases were reported on Form four filings with the SEC.",
             "The figures here are as filed, and filings can be amended later."]
    cards_ = [{"title": "First filing", "body": "CEO purchase, $74.4M as filed."},
              {"title": "Second filing", "body": "CEO purchase, $12.0M as filed."},
              {"title": "The source", "body": "SEC Form 4 filings."},
              {"title": "About the figures", "body": "As filed; filings can be amended."}]
    script = WorkerScript.model_validate({
        "hook": "Two chief executives disclosed buying their own stock last week.", "video_script": lines,
        "cards": cards_, "carousel_slides": [],
        "disclaimer_card": post_copy.disclaimer_card(run_date, authorship="template"),
        "outlets": ["bluesky", "facebook", "tiktok", "x"],
        "post_formats": {"bluesky": "image", "facebook": "text", "tiktok": "video", "x": "image"},
        "image_post": {"title": "A table of CEO purchases disclosed last week",
                       "paragraphs": ["Two rows, largest first.", "Source: SEC Form 4 filings."]},
        "image_footer": footer, "content_class": "C", "authorship": "template", "series": "ceo_buys",
        "video_layout": "per_line", "opening_card": opening, "image_spec": image_spec, "logos": entries,
    }).model_dump()
    be = _TemplateBackend(script_states=[{"status": "accepted", "source_ref": "news:ceo_buys:2026-09-10",
                                          "template_id": "ceo_buys", "script": script}])
    return be, script, _Downloads(objects)


def _video_fake(seen: Dict[str, Any]):
    def produce(*, workdir, specs, words, narration_seconds, audio_file, fonts_dir, logo_path, threads,
                heartbeat, run_id, max_seconds, layout_engine, hook_card=False):
        from marketing import cards
        from marketing import voice as vc

        seen.update(hook_card=hook_card, kinds=[s.kind for s in specs],
                    opening_logos={a.key: (a.path is not None and Path(a.path).is_file()) for a in specs[0].logos})
        # the opening card really renders with its logo/wordmark plate (Pillow, no ffmpeg)
        cards.render_card(specs[0], font_path=str(Path(fonts_dir) / "Inter-Bold.ttf"), layout_engine=layout_engine)
        return _FAKE_MP4, narration_seconds + vc.DISCLAIMER_CARD_SECONDS, list(range(len(specs)))
    return produce


def test_a_template_day_draws_logos_once_and_declares_only_what_the_server_allows(m, monkeypatch, env, tmp_path):
    from app.schemas.marketing import VIDEO_BRAND_TEXT, AssetRegisterRequest
    from app.services.marketing import template_onscreen as tos
    from marketing import cards
    from marketing import render as rd

    be, script, dl = _template_world(tmp_path)
    seen: Dict[str, Any] = {}
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(rd, "download", dl)
    monkeypatch.setattr(rd, "produce_video", _video_fake(seen))
    assert m.main() == 0, be.patches[-1]
    # the per-line video: [opening] + one card per line + [disclaimer], opening over the hook
    assert seen["hook_card"] is True
    assert seen["kinds"] == ["opening", "text", "text", "text", "text", "disclaimer"]
    assert seen["opening_logos"] == {"GME": True}                    # verified file, alive during the render
    # each logo fetched ONCE for the stage, shared by the video and the image
    logo_calls = [u for u, _k in dl.calls if "/logos/" in u]
    assert sorted(logo_calls) == sorted(e["url"] for e in script["logos"])
    (card,) = [a for a in be.assets.values() if a["kind"] == "card"]
    (video,) = [a for a in be.assets.values() if a["kind"] == "video"]
    # the image: the server's template branch (run_service D12) — image_strings ∪ {footer}, footer required,
    # the alt text never
    img_md = card["metadata"]
    allowed = set(tos.image_strings(script["image_spec"], script["logos"])) | {script["image_footer"]}
    assert set(img_md["onscreen_text"]) <= allowed and script["image_footer"] in img_md["onscreen_text"]
    assert not ({script["image_post"]["title"], *script["image_post"]["paragraphs"]} & set(img_md["onscreen_text"]))
    assert img_md["image_layout"] == "rows" and img_md["card_version"] == cards.CARD_RENDER_VERSION == "cards/v3"
    assert img_md["logos"] == [[e["key"], e["sha256"]] for e in script["logos"]]      # both drawn as logos
    # the video: brand text ∪ card titles/bodies ∪ disclaimer ∪ opening strings, disclaimer required
    vid_md = video["metadata"]
    v_allowed = set(VIDEO_BRAND_TEXT) | {c[k] for c in script["cards"] for k in ("title", "body")} | {
        script["disclaimer_card"]} | set(tos.opening_strings(script["opening_card"], script["logos"]))
    assert set(vid_md["onscreen_text"]) <= v_allowed and script["disclaimer_card"] in vid_md["onscreen_text"]
    assert "GameStop" not in vid_md["onscreen_text"]                 # the logo drew, not its wordmark
    assert vid_md["video_layout"] == "per_line" and vid_md["render_version"] == rd.RENDER_STAGE_VERSION
    for reg in be.registered:
        AssetRegisterRequest.model_validate(reg)
    ((posts,),) = [list(b.values()) for b in be.posts_bodies]
    assert {p["platform"]: p["format"] for p in posts} == script["post_formats"]
    assert be.run["status"] == "media_ready"


def test_a_tampered_logo_is_a_wordmark_on_both_media(m, monkeypatch, env, tmp_path, caplog):
    from app.services.marketing import template_onscreen as tos
    from marketing import render as rd

    be, script, dl = _template_world(tmp_path, tamper="GME")
    seen: Dict[str, Any] = {}
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(rd, "download", dl)
    monkeypatch.setattr(rd, "produce_video", _video_fake(seen))
    caplog.set_level(logging.ERROR, logger="marketing.logos")
    assert m.main() == 0
    assert seen["opening_logos"] == {"GME": False}
    assert any("GME" in r.getMessage() and "sha256" in r.getMessage() for r in caplog.records)
    (card,) = [a for a in be.assets.values() if a["kind"] == "card"]
    (video,) = [a for a in be.assets.values() if a["kind"] == "video"]
    assert "GameStop" in video["metadata"]["onscreen_text"]          # the opening's wordmark, declared
    assert "GameStop" in tos.opening_strings(script["opening_card"], script["logos"])
    assert card["metadata"]["logos"] == [["GME", None], ["NVDA", script["logos"][1]["sha256"]]]
    assert be.run["status"] == "media_ready"


def test_a_template_script_with_a_contract_breach_fails_the_run_loudly(m, monkeypatch, env, tmp_path):
    from marketing import render as rd

    for over, needle in (({"image_spec": None}, "image_spec"), ({"video_layout": None}, "video_layout"),
                         ({"opening_card": None}, "opening_card"), ({"authorship": "robot"}, "authorship")):
        be, script, dl = _template_world(tmp_path)
        be.script_states[0]["script"] = dict(script, **over)
        _wire(m, monkeypatch, be)
        monkeypatch.setattr(rd, "download", dl)
        monkeypatch.setattr(rd, "produce_video", _video_fake({}))
        assert m.main() == 1, over
        closing = _closing_patch(be)
        assert closing["status"] == "failed" and needle in closing["last_error"], (over, closing)
        assert be.posts_bodies == [] and not [a for a in be.assets.values() if a["kind"] in ("card", "video")]


def test_a_lesson_day_calls_the_drop_1_render_keyword_for_keyword(m, monkeypatch, env):
    """A lesson never passes hook_card (the drop-1 fakes and callers keep working) and fetches no logo."""
    from marketing import render as rd

    calls: List[Dict[str, Any]] = []
    downloads: List[str] = []

    def spy(**kw):
        calls.append(kw)
        return _fake_produce_video(**kw)

    be = _video_backend()
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(rd, "produce_video", spy)
    monkeypatch.setattr(rd, "download", lambda url, **k: downloads.append(url) or _FAKE_M4A)
    assert m.main() == 0
    (kw,) = calls
    assert "hook_card" not in kw and [s.kind for s in kw["specs"]] == ["text", "text", "disclaimer"]
    assert len(downloads) == 1 and "/logos/" not in downloads[0]     # the narration only


def test_an_image_only_template_day_places_the_bucket_from_the_manifest_and_never_narrates(
        m, monkeypatch, env, tmp_path):
    """No video outlet → no narration; the bucket origin then comes from another ready asset of the
    run (the preflight manifest), so the logos still verify and draw."""
    from app.services.marketing import template_onscreen as tos
    from marketing import render as rd
    from marketing import voice as vc

    be, script, dl = _template_world(tmp_path)
    script = dict(script, outlets=["bluesky", "x"], post_formats={"bluesky": "image", "x": "image"})
    be.script_states[0]["script"] = script
    _wire(m, monkeypatch, be)
    monkeypatch.setattr(rd, "download", dl)
    monkeypatch.setattr(vc, "run_child", lambda *a, **k: (_ for _ in ()).throw(AssertionError("narrated")))
    assert m.main() == 0
    assert not [a for a in be.assets.values() if a["kind"] in ("audio", "video")]
    (card,) = [a for a in be.assets.values() if a["kind"] == "card"]
    assert card["metadata"]["logos"] == [[e["key"], e["sha256"]] for e in script["logos"]]
    allowed = set(tos.image_strings(script["image_spec"], script["logos"])) | {script["image_footer"]}
    assert set(card["metadata"]["onscreen_text"]) <= allowed
    assert sorted(u for u, _k in dl.calls) == sorted(e["url"] for e in script["logos"])
    assert be.run["status"] == "media_ready"


# ── review round 2: the logo download's phase caps, and the bytes counted are the wire's ────────────


def _serve_once(delay_headers: float, body: bytes):
    """A one-shot loopback HTTP server that waits `delay_headers` seconds after the request before it
    sends ANY response byte (a cold CDN fetching the object from its origin), then answers 200 with
    `body`. Returns (port, stop, thread, server)."""
    import socket
    import threading

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    stop = threading.Event()

    def serve():
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            if stop.wait(delay_headers):
                return
            try:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: "
                             + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server.getsockname()[1], stop, thread, server


def test_a_logo_whose_first_byte_takes_1_5_seconds_still_draws(tmp_path, caplog):
    """Review round 2 (low): every httpx phase used to get share / 4 = 1.25 s, so a cold CDN that took
    1.3 s to its first byte turned a real logo into a wordmark with ~59 s of the budget unused. Now
    connect / TLS / write keep the quarter and every read gets the rest of the share (3.75 s): the real
    `render.download`, over a loopback socket whose headers come after 1.5 s, returns the logo, which
    verifies and is drawn. Mutation-checked by hand (2026-10-09): with the old even split this is a
    wordmark (httpx ReadTimeout after 1.25 s) and the test fails."""
    pytest.importorskip("PIL")
    import time as _time

    from marketing import logos as lg

    rd = _real_download()
    data = _png_bytes()
    entry = _logo_entry("AAA", "Alpha", data)
    port, stop, thread, server = _serve_once(1.5, data)
    seen: List[Dict[str, Any]] = []

    def download(url, **kw):              # the URL resolve_logos verified, served from the loopback host
        assert url == entry["url"]
        seen.append(kw)
        return rd.download(f"http://127.0.0.1:{port}/logos/x.png", **kw)

    caplog.set_level(logging.INFO, logger="marketing.logos")
    started = _time.monotonic()
    try:
        out = lg.resolve_logos({"logos": [entry]}, bucket_origin=_TPL_HOST, download=download, dest_dir=tmp_path)
    finally:
        stop.set()
        thread.join(timeout=5)
        server.close()
    assert out["AAA"] is not None and out["AAA"].read_bytes() == data, [r.getMessage() for r in caplog.records]
    assert _time.monotonic() - started >= 1.5                              # it really waited for the headers
    (kw,) = seen
    assert kw["max_seconds"] == 5.0 and kw["timeout"] == 1.25 and kw["read_timeout"] == 3.75


def test_the_logo_phase_caps_split_each_share_and_every_download_still_ends_inside_the_budget(tmp_path):
    """Connect / TLS / write: share / DOWNLOAD_PHASES each (never above the share); every read: the
    rest of the share. Every download here takes the worst a bounded one can
    (`worst_case_download_seconds` = share + one read = 1.75 × share) and still ends before the shared
    budget does, and the step's worst case still fits the render stage's margin."""
    from marketing import logos as lg

    data = _png_bytes()
    entries = [_logo_entry(f"K{i}", f"Company {i}", data) for i in range(14)]
    clock = {"t": 0.0}
    calls: List[Dict[str, float]] = []

    def worst(url, **kw):
        share = kw["max_seconds"]
        calls.append({"start": clock["t"], "share": share})
        assert kw["timeout"] == lg.connect_seconds(share) == share / 4 <= share
        assert kw["read_timeout"] == lg.read_seconds(share) == share - share / 4
        clock["t"] += lg.worst_case_download_seconds(share)
        return data

    out = lg.resolve_logos({"logos": entries}, bucket_origin=_TPL_HOST, download=worst, dest_dir=tmp_path,
                           monotonic=lambda: clock["t"])
    assert clock["t"] < lg.LOGOS_BUDGET_SECONDS
    assert calls and calls[0]["share"] == lg.LOGO_DOWNLOAD_TIMEOUT_SECONDS
    assert lg.worst_case_download_seconds(5.0) == 5.0 + 3.75
    for c in calls:
        left = lg.LOGOS_BUDGET_SECONDS - c["start"]
        assert c["start"] + lg.worst_case_download_seconds(c["share"]) <= lg.LOGOS_BUDGET_SECONDS
        assert lg.worst_case_download_seconds(c["share"]) <= 0.875 * left + 1e-9
    assert sum(1 for p in out.values() if p) == len(calls)


def test_the_render_download_gives_reads_their_own_cap_and_keeps_the_old_one_by_default(monkeypatch):
    """`timeout` caps the pool wait, connect and the write; `read_timeout` (default: `timeout`, the
    narration download's call is unchanged) caps every read."""
    rd = _real_download()
    real = httpx.Client
    seen: List[Any] = []

    def client(**kw):
        seen.append(kw["timeout"])
        return real(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"ok")), **kw)

    monkeypatch.setattr(httpx, "Client", client)
    assert rd.download("https://sb.example/a.png", max_bytes=10, timeout=1.25, read_timeout=3.75,
                       max_seconds=5.0) == b"ok"
    assert rd.download("https://sb.example/a.m4a", max_bytes=10, timeout=60) == b"ok"
    first, second = seen
    assert (first.connect, first.write, first.pool, first.read) == (1.25, 1.25, 1.25, 3.75)
    assert (second.connect, second.write, second.pool, second.read) == (60, 60, 60, 60)


class _CountedBody(httpx.SyncByteStream):
    """A streamed response body that records how many of its parts were read."""

    def __init__(self, parts: List[bytes]):
        self.parts = parts
        self.read = 0

    def __iter__(self):
        for part in self.parts:
            self.read += 1
            yield part


@pytest.mark.parametrize("encoding", ["gzip", "GZIP", "br", "deflate", "zstd", "identity, gzip", " gzip "])
def test_the_render_download_asks_for_identity_and_refuses_an_encoded_body_unread(monkeypatch, encoding):
    """Round 1's note on LOGO-2: `render.download` counted DECODED bytes, so a small gzip body could
    inflate far past the cap before the cap saw it. It now asks for `Accept-Encoding: identity` and
    refuses a content-encoded answer before reading a byte of its body."""
    import gzip

    rd = _real_download()
    real = httpx.Client
    body = _CountedBody([gzip.compress(b"\0" * 2_000_000)])
    asked: List[str] = []

    def handler(req):
        asked.append(req.headers.get("accept-encoding", ""))
        return httpx.Response(200, headers={"content-encoding": encoding}, stream=body)

    monkeypatch.setattr(httpx, "Client", functools.partial(real, transport=httpx.MockTransport(handler)))
    with pytest.raises(rd.RenderInputError, match="encoded response, refused unread"):
        rd.download("https://sb.example/a.png", max_bytes=512_000, timeout=1.0, max_seconds=5.0)
    assert asked == ["identity"] and body.read == 0


@pytest.mark.parametrize("headers", [{}, {"content-encoding": "identity"}, {"content-encoding": " Identity "}])
def test_the_render_download_reads_an_identity_body_raw_and_caps_the_wire_bytes(monkeypatch, headers):
    """The body is read RAW (iter_raw): the bytes counted against the cap are the bytes on the wire.
    `iter_bytes` (the decoding reader) is made to fail here, so reading through it again turns this
    red; the cap still holds at one byte over."""
    rd = _real_download()
    real = httpx.Client

    def no_decoding(self, *a, **k):
        raise AssertionError("the body was read through the decoding iterator")

    monkeypatch.setattr(httpx.Response, "iter_bytes", no_decoding)
    parts = [b"ab", b"cd"]
    monkeypatch.setattr(httpx, "Client", functools.partial(real, transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers=headers, stream=_CountedBody(list(parts))))))
    assert rd.download("https://sb.example/a.png", max_bytes=4, timeout=1.0, max_seconds=5.0) == b"abcd"
    with pytest.raises(rd.RenderInputError, match="exceeded 3 bytes"):
        rd.download("https://sb.example/a.png", max_bytes=3, timeout=1.0, max_seconds=5.0)
