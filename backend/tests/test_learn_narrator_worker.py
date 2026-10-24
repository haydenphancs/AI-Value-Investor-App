"""
`backend/learn_narrator/` — the standalone Learn narrator worker (plan "Wiser weekly content
engine" §Phase 0 + §1.8).

Hermetic and model-free by design: this Mac has crashed twice from heavy local work, so nothing
here renders, aligns, builds an image or runs the bench. The heavy paths are covered by AST pins
(the Chatterbox call shapes, the constants copied from the catalog scripts, the vendored code) and
by fakes (the child runner, the HTTP server, the measuring functions). What only the real image can
prove — the bench numbers, the smoke render — is the owner's run (learn_narrator/RUNBOOK.md).

Sections:
  1. standalone-ness (no app.*, heavy imports only inside functions, a fresh interpreter)
  2. Chatterbox pins (exaggeration on every generate(), constants = clone_learn_audio.py, the ref sha)
  3. the per-block gate and the render loop's re-render
  4. assembly maths (atempo, pauses, offsets) and the per-block windows (offset ÷ atempo)
  5. alignment helpers (30 s emission windows, targets, word spans, the MMS_FA ban)
  6. timings (the server's split pinned, the server's gate mirrored, Kokoro's vendored code, the table)
  7. verify (pinned to verify_learn_voice.py, bands)
  8. threads, the child runner
  9. the HTTP client's status table against a fake server
 10. the whole tick against a fake API and fake children
 11. image, Railway record, constraints, bake lists
 12. the bench (refusals, /tmp only, fixture, grading)
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
PKG = BACKEND / "learn_narrator"
SCRIPTS = BACKEND / "scripts"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from learn_narrator import align as al  # noqa: E402
from learn_narrator import api, bench, runtime, verify  # noqa: E402
from learn_narrator import main as m  # noqa: E402
from learn_narrator import timings as tm  # noqa: E402
from learn_narrator.engines import chatterbox as cb  # noqa: E402
from learn_narrator.engines import kokoro as kk  # noqa: E402

REF_SHA256 = "7c99bdcefdd6b1e673d10072e78a84293d065a7587869109b4e2a05f220eb1ae"
#: torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H.get_labels() (torchaudio 2.6.0).
W2V_LABELS = ('-', '|', 'E', 'T', 'A', 'O', 'N', 'I', 'H', 'S', 'R', 'D', 'L', 'U', 'M', 'W', 'C', 'F',
              'G', 'Y', 'P', 'B', 'V', 'K', "'", 'X', 'J', 'Q', 'Z')
W2V_DICT = {c: i for i, c in enumerate(W2V_LABELS)}
HEAVY = {"torch", "torchaudio", "numpy", "chatterbox", "kokoro", "librosa", "soundfile", "misaki",
         "scipy", "huggingface_hub", "transformers", "spacy"}


def _files() -> List[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _top(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    raise AssertionError(f"{name} not found at module level")


def _is_doc(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _fn_dump(node: ast.AST, *, drop: tuple = ()) -> str:
    """A function/class with its docstring and the statements `drop` (ast.dump strings) removed."""
    node = ast.parse(ast.unparse(node)).body[0]
    body = [s for s in node.body if not _is_doc(s) and ast.dump(s) not in drop]
    node.body = body or [ast.Pass()]
    return ast.dump(node)


def _literal(path: Path, name: str):
    return ast.literal_eval(_top(_tree(path), name))


# ══ 1. standalone-ness ═══════════════════════════════════════════════════════

_NAME_IMPORTERS = {"import_module", "__import__", "run_module"}
_CODE_LOADERS = {"run_path", "spec_from_file_location", "SourceFileLoader", "exec_module", "load_module"}
_APP_DOTTED = re.compile(r"app(?:\.[A-Za-z_]\w*)+")


def _module_name(path: Path) -> tuple:
    parts = ["learn_narrator", *path.relative_to(PKG).with_suffix("").parts]
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts.pop()
    return ".".join(parts), is_pkg


def worker_imports(src: str, module: str, is_pkg: bool = False):
    """(imported dotted names, problems) — the marketing worker's AST scan, condensed: every import
    statement at any depth (relative ones resolved), literal dynamic imports, and anything that
    loads code by path or from text fails closed."""
    tree = ast.parse(src)
    pkg = module.split(".") if is_pkg else module.split(".")[:-1]
    imported, problems = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level - 1 >= len(pkg):
                    problems.append((node.lineno, "relative import beyond the package"))
                    continue
                base = ".".join(pkg[: len(pkg) - (node.level - 1)] + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            imported.append((node.lineno, base))
            imported += [(node.lineno, f"{base}.{a.name}") for a in node.names if a.name != "*"]
        elif isinstance(node, ast.Call):
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if isinstance(node.func, ast.Name) and fn in {"exec", "eval", "compile"}:
                problems.append((node.lineno, f"{fn}() runs source text"))
            elif fn in _CODE_LOADERS:
                problems.append((node.lineno, f"{fn}() loads code by path"))
            elif fn in _NAME_IMPORTERS:
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    imported.append((node.lineno, arg.value))
                else:
                    problems.append((node.lineno, f"dynamic {fn}()"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _APP_DOTTED.fullmatch(node.value):
            problems.append((node.lineno, f"string {node.value!r} names an app module"))
    return imported, problems


def _app_violations(imported, problems) -> List[str]:
    return ([f"{ln}: imports {n}" for ln, n in imported if n == "app" or n.startswith("app.")]
            + [f"{ln}: {what}" for ln, what in problems])


def test_the_worker_package_imports_nothing_from_app():
    files = _files()
    assert len(files) >= 10, files
    seen_httpx = False
    for py in files:
        module, is_pkg = _module_name(py)
        imported, problems = worker_imports(py.read_text(encoding="utf-8"), module, is_pkg)
        assert _app_violations(imported, problems) == [], f"{py.relative_to(PKG)}: the worker holds no app config"
        seen_httpx |= py.name == "api.py" and "httpx" in {n for _, n in imported}
    assert seen_httpx  # anti-vacuity: the scan really parsed api.py's imports


@pytest.mark.parametrize("src", [
    "from app.config import settings",
    "import app.database",
    "def f():\n    from app.services.learn_weekly import narration\n",
    "import importlib\nimportlib.import_module('app.config')",
    "__import__('app.main')",
    "import importlib\nimportlib.import_module(name)",
    "exec('import app')",
    "target = 'app.config'",
    "from .. import x",
])
def test_the_scanner_flags(src):
    imported, problems = worker_imports(src, "learn_narrator.main")
    assert _app_violations(imported, problems), src


@pytest.mark.parametrize("src", [
    "import httpx",
    "from learn_narrator import timings as tm",
    "from . import runtime",
    '"""Never import app.config here."""',
    "MODELS = '/app/models'",
])
def test_the_scanner_passes(src):
    imported, problems = worker_imports(src, "learn_narrator.main")
    assert _app_violations(imported, problems) == []


def test_heavy_imports_live_inside_functions_only():
    """The parent never imports torch & co: a module-level heavy import anywhere in the package
    would load it into the cron process (and break the fresh-interpreter test below)."""
    offenders = []
    heavy_inside = 0
    for py in _files():
        tree = _tree(py)
        for node in tree.body:
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module or ""]
            offenders += [f"{py.name}: {n}" for n in names if n.split(".")[0] in HEAVY]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in tree.body:
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                heavy_inside += sum(1 for n in mods if n.split(".")[0] in HEAVY)
    assert offenders == []
    assert heavy_inside >= 8  # anti-vacuity: the engines really do import their stack lazily


def test_the_package_imports_in_a_tree_without_app(tmp_path):
    """The Docker layout: only `learn_narrator/` exists. Every module imports in a fresh
    interpreter, loads no `app` module by any route, and loads none of the heavy stack."""
    shutil.copytree(PKG, tmp_path / "learn_narrator", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    mods = ["main", "api", "runtime", "timings", "align", "verify", "child", "bench", "bake",
            "engines", "engines.chatterbox", "engines.kokoro"]
    code = (
        "import importlib, importlib.util, json, sys\n"
        "assert importlib.util.find_spec('app') is None, 'app is importable: the check is vacuous'\n"
        f"[importlib.import_module('learn_narrator.' + m) for m in {mods!r}]\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "LEARN_NARRATOR_LOG_LEVEL": "WARNING"})
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                          timeout=120, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    loaded = set(json.loads(proc.stdout.strip().splitlines()[-1]))
    assert {f"learn_narrator.{x}" for x in mods} <= loaded
    assert [n for n in loaded if n == "app" or n.startswith("app.")] == []
    assert sorted(n for n in loaded if n.split(".")[0] in HEAVY) == []


def test_httpx_and_httpcore_log_at_warning_or_above():
    """A signed upload URL is a write credential; httpx logs request lines (URL included) at INFO."""
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    src = (PKG / "main.py").read_text()
    assert re.search(r'for _noisy in \("httpx", "httpcore"\):\s*\n\s*logging\.getLogger\(_noisy\)\.setLevel\(logging\.WARNING\)', src)


# ══ 2. Chatterbox pins ═══════════════════════════════════════════════════════


def _calls(name: str) -> List[ast.Call]:
    out = []
    for py in _files():
        for node in ast.walk(_tree(py)):
            if isinstance(node, ast.Call):
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if fn == name:
                    out.append(node)
    return out


def test_every_generate_call_passes_exaggeration():
    """chatterbox/tts.py:208-231: `generate` rebuilds the emotion conditioning whenever its
    `exaggeration` argument (DEFAULT 0.5) differs from the prepared one — leaving it out silently
    renders at 0.5 after prepare_conditionals(…, exaggeration=0.65)."""
    calls = _calls("generate")
    assert len(calls) >= 2, "anti-vacuity: the engine and the smoke step both call generate()"
    for c in calls:
        kws = {k.arg for k in c.keywords}
        assert "exaggeration" in kws, ast.unparse(c)
        assert "cfg_weight" in kws, ast.unparse(c)
        assert "audio_prompt_path" not in kws, "conditioning is prepared once, never per call"


def test_prepare_conditionals_is_called_with_exaggeration():
    calls = _calls("prepare_conditionals")
    assert len(calls) >= 2
    for c in calls:
        assert "exaggeration" in {k.arg for k in c.keywords}, ast.unparse(c)


def test_the_generate_scanner_catches_a_call_without_exaggeration():
    bad = ast.parse("model.generate(text, cfg_weight=0.4)").body[0].value
    assert "exaggeration" not in {k.arg for k in bad.keywords}


@pytest.mark.parametrize("name", ["EXAG", "CFG", "TARGET_WPM", "BLOCK_PAUSE", "SENT_PAUSE", "SR"])
def test_voice_constants_equal_the_catalog_script(name):
    """clone_learn_audio.py voiced the catalog (block mode, approved 2026-06-24)."""
    script = _literal(SCRIPTS / "clone_learn_audio.py", name)
    worker = _literal(PKG / "engines" / "chatterbox.py", name)
    assert worker == script == getattr(cb, name)


def test_the_catalog_reference_name_matches_the_script():
    src = (SCRIPTS / "clone_learn_audio.py").read_text()
    assert f'"data/voice_clone/refs/{cb.REF_NAME}.wav"' in src


def test_the_reference_clip_is_pinned():
    ref = PKG / "assets" / "caydex_voice_achird_v2.wav"
    assert hashlib.sha256(ref.read_bytes()).hexdigest() == REF_SHA256 == cb.REF_SHA256
    assert cb.ref_sha256() == REF_SHA256
    catalog = BACKEND / "data" / "voice_clone" / "refs" / "caydex_voice_achird_v2.wav"
    assert hashlib.sha256(catalog.read_bytes()).hexdigest() == REF_SHA256, "the catalog clip changed"


def test_the_web_side_freezes_the_same_voice_the_worker_pins():
    """app/services/learn_weekly/narration.py freezes CHATTERBOX_VOICE into every job."""
    web = _literal(BACKEND / "app" / "services" / "learn_weekly" / "narration.py", "CHATTERBOX_VOICE")
    assert web == {"exaggeration": cb.EXAG, "cfg_weight": cb.CFG, "target_wpm": cb.TARGET_WPM, "ref": cb.REF_NAME}
    v = cb.voice_params(web)
    assert (v.exaggeration, v.cfg_weight, v.target_wpm) == (cb.EXAG, cb.CFG, cb.TARGET_WPM)


@pytest.mark.parametrize("voice", [
    {"ref": "someone_else"},
    {"exaggeration": 5.0},
    {"exaggeration": True},
    {"cfg_weight": -0.1},
    {"target_wpm": 400},
    {"exaggeration": "0.65"},
])
def test_an_unsupported_voice_is_refused(voice):
    with pytest.raises(cb.VoiceUnsupported):
        cb.voice_params(voice)


def test_a_missing_voice_key_takes_the_catalog_value(caplog):
    with caplog.at_level(logging.WARNING):
        v = cb.voice_params({"ref": cb.REF_NAME})
    assert (v.exaggeration, v.cfg_weight, v.target_wpm) == (cb.EXAG, cb.CFG, cb.TARGET_WPM)
    assert "exaggeration" in caplog.text


def test_render_asserts_the_prepared_emotion_and_seeds_each_block():
    src = (PKG / "engines" / "chatterbox.py").read_text()
    loader = ast.unparse(_top(_tree(PKG / "engines" / "chatterbox.py"), "load_voice_model"))
    assert "model.conds.t3.emotion_adv" in loader and "VoiceUnsupported" in loader
    assert "torch.manual_seed(seed)" in loader
    assert 'from_pretrained(device="cpu")' in src.replace("'", '"')
    # The assembling child re-checks every saved block's emotion against the job's.
    render = ast.unparse(_top(_tree(PKG / "engines" / "chatterbox.py"), "render"))
    assert "emotions" in render and "VoiceUnsupported" in render


# ══ 2b. rendering in parts (the 2026-10-24 memory kill) ══════════════════════════


def _pt_blocks(n: int) -> List[Dict[str, Any]]:
    return [{"i": i, "text": f"Block {i} says something plain about tape and filters.", "role": "body"}
            for i in range(n)]


def _pt_synth(calls: List[Any]):
    """Deterministic in (text, seed) — like a seeded model — and records every call."""
    import numpy as np

    def synth(text, seed):
        calls.append((text, seed))
        n = int(cb.SR * 0.45 * len(text.split()))
        rng = np.random.default_rng(seed)
        return rng.standard_normal(n).astype(np.float32) * 0.1, n
    return synth


def _pt_loader(calls: List[Any], loads: List[int], emotion: float = cb.EXAG, fail_at: Optional[int] = None):
    def load(job, *, threads, progress=print):
        loads.append(1)
        inner = _pt_synth(calls)

        def synth(text, seed):
            if fail_at is not None and len(calls) >= fail_at:
                raise MemoryError("killed")
            return inner(text, seed)
        return synth, emotion
    return load


def _pt_job(n: int) -> Dict[str, Any]:
    return {"engine": "chatterbox", "voice": {}, "blocks": _pt_blocks(n), "seed": cb.SEED}


def test_a_saved_block_round_trips(tmp_path):
    import numpy as np
    calls: List[Any] = []
    b = cb.render_blocks(_pt_blocks(1), _pt_synth(calls))[0]
    cb.save_block(tmp_path, b, _pt_blocks(1)[0]["text"], cb.EXAG)
    got, emotion = cb.load_block(tmp_path, _pt_blocks(1)[0])
    assert emotion == cb.EXAG and (got.i, got.words, got.sr) == (b.i, b.words, b.sr)
    assert [(c.text, c.seed, c.samples, c.spoken_words, c.rerendered, c.first_reason) for c in got.calls] == \
        [(c.text, c.seed, c.samples, c.spoken_words, c.rerendered, c.first_reason) for c in b.calls]
    assert all(np.array_equal(x.audio, y.audio) for x, y in zip(got.calls, b.calls))
    assert not list(tmp_path.rglob("*.tmp"))                    # written atomically


@pytest.mark.parametrize("spoil", ["text", "pipeline", "length", "corrupt", "truncated", "missing"])
def test_a_saved_block_that_cannot_be_trusted_is_rendered_again(tmp_path, monkeypatch, spoil, caplog):
    import numpy as np
    block = _pt_blocks(1)[0]
    b = cb.render_blocks([block], _pt_synth([]))[0]
    cb.save_block(tmp_path, b, block["text"], cb.EXAG)
    path = cb.part_path(tmp_path, 0)
    if spoil == "text":
        block = {**block, "text": "Another sentence entirely."}
    elif spoil == "pipeline":
        monkeypatch.setattr(cb, "PIPELINE_VERSION", "chatterbox-0.1.7/block/aac96k/v2")
    elif spoil == "length":
        b.calls[0].samples += 1
        cb.save_block(tmp_path, b, block["text"], cb.EXAG)
    elif spoil == "corrupt":
        path.write_bytes(b"not an npz")
    elif spoil == "truncated":                  # a zip cut short: zipfile.BadZipFile, not OSError
        path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    else:
        path.unlink()
    assert cb.load_block(tmp_path, block) is None
    if spoil != "missing":
        assert f"saved block 0" in caplog.text


def test_parts_make_the_same_audio_as_one_pass(tmp_path):
    """Every block keeps its own seed, so rendering in slices (one model load each) gives the
    renders one process would: the same calls, seeds and samples, block for block."""
    import numpy as np
    job = _pt_job(7)
    one_calls: List[Any] = []
    one = cb.render_blocks(job["blocks"], _pt_synth(one_calls), seed=cb.SEED)
    calls: List[Any] = []
    loads: List[int] = []
    for start, end in ((0, 3), (3, 6), (6, 7)):
        cb.render_saved(job, tmp_path, job["blocks"][start:end], threads=1, load_model=_pt_loader(calls, loads))
    assert len(loads) == 3 and calls == one_calls
    asm_loads: List[int] = []
    rendered, emotions, fresh = cb.render_saved(job, tmp_path, job["blocks"], threads=1,
                                                load_model=_pt_loader([], asm_loads))
    assert fresh == 0 and asm_loads == [] and emotions == [cb.EXAG] * 7  # assembly loads no model
    for a, b in zip(rendered, one):
        assert [c.seed for c in a.calls] == [c.seed for c in b.calls]
        assert all(np.array_equal(x.audio, y.audio) for x, y in zip(a.calls, b.calls))


def test_a_killed_part_keeps_its_finished_blocks_and_resumes_after_them(tmp_path):
    job = _pt_job(5)
    calls: List[Any] = []
    with pytest.raises(MemoryError):
        cb.render_saved(job, tmp_path, job["blocks"], threads=1, load_model=_pt_loader(calls, [], fail_at=3))
    assert sorted(p.name for p in (tmp_path / cb.PARTS_DIR).glob("*.npz")) == \
        ["block-0000.npz", "block-0001.npz", "block-0002.npz"]
    again: List[Any] = []
    rendered, _, fresh = cb.render_saved(job, tmp_path, job["blocks"], threads=1, load_model=_pt_loader(again, []))
    assert fresh == 2 and [t for t, _ in again] == [b["text"] for b in job["blocks"][3:]]
    assert [b.i for b in rendered] == [0, 1, 2, 3, 4]


def test_a_saved_block_at_another_emotion_is_refused_at_assembly(tmp_path, monkeypatch):
    job = _pt_job(2)
    cb.render_saved(job, tmp_path, job["blocks"][:1], threads=1, load_model=_pt_loader([], [], emotion=0.5))
    monkeypatch.setattr(cb, "load_voice_model", _pt_loader([], []))
    monkeypatch.setitem(sys.modules, "soundfile", type(sys)("soundfile"))
    with pytest.raises(cb.VoiceUnsupported, match="emotion_adv"):
        cb.render(job, tmp_path, threads=1)


@pytest.mark.parametrize("part", [None, [0], [0, 0], [2, 1], [0, 9], [-1, 2], [True, 2], ["0", "2"]])
def test_a_part_must_be_a_slice_of_the_pt_blocks(part):
    with pytest.raises(ValueError):
        cb.part_range({**_pt_job(4), "part": part})
    assert cb.part_range({**_pt_job(4), "part": [1, 4]}) == (1, 4)


def _pt_runner(seen: List[Any], *, oom: Optional[Dict[int, int]] = None, fail: Optional[Dict[int, str]] = None):
    """A fake `runtime.run_child` for render_in_parts: part k is killed `oom[k]` times, or fails with
    `fail[k]`; the assembly returns a render body."""
    oom, fail = dict(oom or {}), dict(fail or {})

    def runner(mode, job_path, out_dir, *, timeout, threads, heartbeat):
        body = json.loads(job_path.read_text())
        seen.append((mode, body.get("part"), timeout))
        if mode == "render_part":
            k = int(job_path.stem.rsplit("-", 1)[1])
            if oom.get(k):
                oom[k] -= 1
                raise runtime.ChildFailed("render_part_oom", "render_part child killed (exit -9)")
            if k in fail:
                raise runtime.ChildFailed(fail[k], "boom")
            return {"part": body["part"]}
        return {"audio": "narration.m4a", "wall_seconds": 1.0}
    return runner


def _pt_parts(tmp_path, n_blocks, runner, budget=3600.0, clock=None):
    job = m.Job(id="j", round=1, engine="chatterbox", voice={}, pipeline_version="learn-narration/v1", attempts=1,
                claim_nonce="n", blocks=_pt_blocks(n_blocks))
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps({"engine": "chatterbox", "blocks": _pt_blocks(n_blocks), "out_dir": str(tmp_path)}))
    return m.render_in_parts(runner, job, job_path, tmp_path, threads=2, budget=budget, heartbeat=lambda: None,
                             **({"clock": clock} if clock else {}))


def test_the_parts_cover_every_block_once_then_assemble(tmp_path):
    seen: List[Any] = []
    out = _pt_parts(tmp_path, 75, _pt_runner(seen))
    parts = [p for mode, p, _ in seen if mode == "render_part"]
    assert parts[0][0] == 0 and parts[-1][1] == 75 and len(parts) == -(-75 // cb.RENDER_PART_BLOCKS)
    assert all(b - a <= cb.RENDER_PART_BLOCKS for a, b in parts)
    assert all(parts[k][1] == parts[k + 1][0] for k in range(len(parts) - 1))
    assert [mode for mode, _, _ in seen][-1] == "render" and seen[-1][1] is None
    assert "wall_seconds" in out and out["audio"] == "narration.m4a"


def test_a_killed_part_runs_once_more_then_the_job_goes_on(tmp_path, caplog):
    seen: List[Any] = []
    _pt_parts(tmp_path, 30, _pt_runner(seen, oom={1: 1}))
    modes = [(mode, tuple(p) if p else None) for mode, p, _ in seen]
    n = cb.RENDER_PART_BLOCKS
    assert modes == [("render_part", (0, n)), ("render_part", (n, 2 * n)), ("render_part", (n, 2 * n)),
                     ("render_part", (2 * n, 30)), ("render", None)]
    assert "running it once more" in caplog.text


def test_a_part_killed_twice_ends_the_job_with_its_code(tmp_path):
    seen: List[Any] = []
    with pytest.raises(runtime.ChildFailed) as e:
        _pt_parts(tmp_path, 30, _pt_runner(seen, oom={0: 2}))
    assert e.value.code == "render_part_oom" and len(seen) == 2


def test_any_other_part_failure_is_not_retried(tmp_path):
    seen: List[Any] = []
    with pytest.raises(runtime.ChildFailed) as e:
        _pt_parts(tmp_path, 30, _pt_runner(seen, fail={0: "block_gate"}))
    assert e.value.code == "block_gate" and len(seen) == 1


def test_every_part_shares_one_budget(tmp_path):
    now = [0.0]

    def clock():
        return now[0]
    seen: List[Any] = []
    inner = _pt_runner(seen)

    def slow(mode, job_path, out_dir, *, timeout, threads, heartbeat):
        out = inner(mode, job_path, out_dir, timeout=timeout, threads=threads, heartbeat=heartbeat)
        now[0] += 1000.0
        return out
    with pytest.raises(runtime.ChildFailed) as e:
        _pt_parts(tmp_path, 75, slow, budget=3500.0, clock=clock)
    assert e.value.code == "render_timeout"
    assert [t for _, _, t in seen] == [3500.0, 2500.0, 1500.0, 500.0]


def _pt_part_job(tmp_path, n: int, part) -> Path:
    job_path = tmp_path / "job.part-01.json"
    job_path.write_text(json.dumps({**_pt_job(n), "part": part, "out_dir": str(tmp_path), "threads": 1}))
    return job_path


def test_the_render_part_child_renders_exactly_its_slice(tmp_path, monkeypatch):
    """Review: nothing ran the real render_part, so slicing [start:] or dispatching to `render`
    (one process again — the 8 GB kill) passed every test."""
    from learn_narrator import child
    calls: List[Any] = []
    loads: List[int] = []
    monkeypatch.setattr(cb, "load_voice_model", _pt_loader(calls, loads))
    job_path = _pt_part_job(tmp_path, 30, [12, 24])
    out = child.run("render_part", job_path)
    blocks = json.loads(job_path.read_text())["blocks"]
    assert [t for t, _ in calls] == [b["text"] for b in blocks[12:24]]
    assert sorted(p.name for p in (tmp_path / cb.PARTS_DIR).glob("*.npz")) == \
        [f"block-{i:04d}.npz" for i in range(12, 24)]
    assert out["part"] == [12, 24] and (out["rendered"], out["reused"]) == (12, 0)
    assert isinstance(out["peak_rss_mb"], int) and out["peak_rss_mb"] > 0
    assert not (tmp_path / "narration.m4a").exists()
    again = child.run("render_part", job_path)                  # a retried part resumes: nothing left
    assert (again["rendered"], again["reused"]) == (0, 12) and len(loads) == 1


def test_a_memory_error_in_a_part_is_that_parts_oom(tmp_path, monkeypatch):
    """Review: `_classify` named every MemoryError `render_oom`, a code render_in_parts never retries."""
    from learn_narrator import child

    def boom(job, *, threads, progress=print):
        raise MemoryError("std::bad_alloc")
    monkeypatch.setattr(cb, "load_voice_model", boom)
    job_path = _pt_part_job(tmp_path, 2, [0, 2])
    assert child.main(["child", "render_part", str(job_path)]) == runtime.CHILD_TYPED_FAILURE
    assert json.loads((tmp_path / "error.json").read_text())["code"] == "render_part_oom"

    class OutOfMemoryError(RuntimeError):          # torch's CPU allocator error, matched by name
        pass
    assert child._classify(OutOfMemoryError("DefaultCPUAllocator"), "render_part")["code"] == "render_part_oom"
    assert child._classify(MemoryError(), "render")["code"] == "render_oom"
    assert child._classify(MemoryError(), "align")["code"] == "align_oom"


@pytest.mark.parametrize("mode_failing", ["render_part", "render"])
def test_the_budget_running_out_inside_a_child_is_always_render_timeout(tmp_path, mode_failing):
    seen: List[Any] = []
    inner = _pt_runner(seen)

    def runner(mode, job_path, out_dir, *, timeout, threads, heartbeat):
        if mode == mode_failing:
            raise runtime.ChildFailed(f"{mode}_timeout", f"{mode} ran over 60 min; last: Sampling")
        return inner(mode, job_path, out_dir, timeout=timeout, threads=threads, heartbeat=heartbeat)
    with pytest.raises(runtime.ChildFailed) as e:
        _pt_parts(tmp_path, 20, runner)
    assert e.value.code == "render_timeout" and "budget" in e.value.detail
    assert (f"blocks 0-{cb.RENDER_PART_BLOCKS}" if mode_failing == "render_part" else "assembly") in e.value.detail


class _BenchRendered(Exception):
    pass


def _bench_args(out: Path, engine: str = "chatterbox"):
    return bench.parse_args(["--out", str(out), "--no-ab-kit", "--engine", engine, "--threads", "1"])


def test_the_bench_renders_chatterbox_the_workers_way(tmp_path, monkeypatch):
    """Review: the Phase-0 bench still rendered in ONE child — the setup the 8 GB limit killed."""
    monkeypatch.setattr(bench, "TMP_ROOT", tmp_path)
    monkeypatch.setattr(m, "check_voice", lambda job, env: None)
    seen: Dict[str, Any] = {}

    def parts(runner, job, job_path, out_dir, **kw):
        seen.update(kw, blocks=len(job.blocks))
        raise _BenchRendered()
    monkeypatch.setattr(m, "render_in_parts", parts)
    with pytest.raises(_BenchRendered):
        bench.run(_bench_args(tmp_path / "run1"), env={})
    assert seen["budget"] == bench.RENDER_TIMEOUT_SECONDS and seen["blocks"] > cb.RENDER_PART_BLOCKS

    def single(mode, *a, **k):                       # Kokoro keeps its one render child
        raise _BenchRendered(mode)
    with pytest.raises(_BenchRendered, match="render"):
        bench.run(_bench_args(tmp_path / "run2", "kokoro"), env={}, runner=single)


def test_the_bench_refuses_a_folder_holding_an_earlier_runs_blocks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench, "TMP_ROOT", tmp_path)
    monkeypatch.setattr(m, "check_voice", lambda job, env: None)
    monkeypatch.setattr(m, "render_in_parts", lambda *a, **k: (_ for _ in ()).throw(_BenchRendered()))
    (tmp_path / "run1" / cb.PARTS_DIR).mkdir(parents=True)
    (tmp_path / "run1" / cb.PARTS_DIR / "block-0000.npz").write_bytes(b"x")
    assert bench.run(_bench_args(tmp_path / "run1"), env={}) == 2
    assert "earlier run" in capsys.readouterr().err


def test_the_child_runs_render_part_for_chatterbox_only(tmp_path):
    from learn_narrator import child
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps({**_pt_job(2), "engine": "kokoro", "part": [0, 2], "out_dir": str(tmp_path)}))
    with pytest.raises(runtime.ChildFailed, match="Chatterbox-only"):
        child.run("render_part", job_path)
    src = (PKG / "child.py").read_text()
    assert '"render_part"' in src.split("def main")[1]


# ══ 3. the per-block gate ════════════════════════════════════════════════════


@pytest.mark.parametrize("words, seconds, ok", [
    (90, 39.5, False),          # at the cap: truncated
    (90, 39.9, False),
    (90, 39.49, True),          # 2.28 w/s, just under the cap
    (18, 10.0, True),           # 1.8 exactly
    (36, 10.0, True),           # 3.6 exactly
    (17, 10.0, False),          # 1.7 w/s — a stall / silence
    (37, 10.0, False),          # 3.7 w/s — a skipped clause
    (3, 0.0, False),            # no audio
    (3, 1.96, True),            # a real 3-word title (1.53 w/s): short calls get the wide band
    (3, 4.0, False),            # ... which still catches a stall
    (7, 1.3, False),            # ... and a skipped word (5.4 w/s)
    (8, 4.0, True),             # 8 spoken words is a normal call: 2.0 w/s
])
def test_the_block_gate(words, seconds, ok):
    reason = cb.block_gate(words, seconds)
    assert (reason is None) is ok, reason
    if seconds >= cb.TRUNCATION_SECONDS:
        assert "truncated" in reason


#: Raw (pre-tempo) block renders cached on the owner's Mac by clone_learn_audio.py (2026-10-22,
#: data/voice_clone/_block_cache, read from the WAV headers): (raw seconds, spoken words). Every one
#: is a real catalog render at the catalog's settings, so the gate must pass all of them.
MEASURED_CATALOG_CALLS = [
    (25.16, 77), (23.28, 70), (14.88, 48), (14.32, 48), (13.84, 39), (13.00, 36), (12.92, 42), (11.96, 35),
    (10.28, 37), (10.24, 35), (10.08, 35), (8.00, 24), (6.36, 17), (4.68, 9), (4.56, 12), (4.16, 14),
    (3.40, 9), (3.08, 6), (2.16, 5), (1.96, 3), (1.88, 6), (1.48, 5), (1.48, 3),
]


def test_the_gate_passes_every_measured_catalog_render():
    assert [(s, w) for s, w in MEASURED_CATALOG_CALLS if cb.block_gate(w, s) is not None] == []


def test_the_kokoro_gate_has_no_truncation_cap():
    assert kk.block_gate(120, 45.0) is None          # Kokoro chunks long text itself
    assert kk.block_gate(10, 45.0) is not None


def _dense() -> str:
    return next(b["text"] for b in bench.load_fixture()["blocks"] if b["source"] == "worst_case:figures_75")


def test_the_figure_dense_worst_case_cannot_fit_one_call():
    """The plan's own worst case: 75 whitespace words, ~128 spoken ("$574.8 billion" is eight) —
    ≈ 47 s at a natural pace, past Chatterbox's 40 s cap. One call per block would truncate it on
    every attempt; `call_groups` splits it at sentence boundaries instead."""
    dense = _dense()
    spoken, tokens = tm.spoken_word_count(dense), len(dense.split())
    assert tokens == 75 and spoken > 120
    assert cb.block_gate(spoken, spoken / 2.7) is not None and "truncated" in cb.block_gate(spoken, spoken / 2.7)
    groups = cb.call_groups(dense)
    assert len(groups) >= 2
    assert " ".join(groups).split() == dense.split()               # no word lost or reordered
    for g in groups:
        sp = tm.spoken_word_count(g)
        assert cb.block_gate(sp, sp / 2.6) is None                 # each call fits, at a natural pace
        assert cb.block_gate(len(g.split()), sp / 2.6) is not None  # counting tokens would wrongly fail it


def test_no_catalog_block_is_split():
    """Every catalog block (≤ 93 words of prose) stays ONE call — block mode, as it was voiced."""
    blocks = _catalog_blocks()
    assert [b for b in blocks if len(cb.call_groups(tm.strip_markup(b))) > 1] == []


@pytest.mark.parametrize("n_sents, words_each", [(2, 70), (3, 50), (7, 25), (12, 15), (40, 6)])
def test_call_groups_are_the_fewest_balanced_calls_that_fit(n_sents, words_each):
    text = " ".join("Word " + " ".join(["word"] * (words_each - 2)) + " end." for _ in range(n_sents))
    groups = cb.call_groups(text)
    assert " ".join(groups).split() == text.split()
    est = [cb.estimate_seconds(g) for g in groups]
    assert all(e <= cb.SPLIT_ESTIMATE_SECONDS for e in est)
    fewest = -(-tm.spoken_word_count(text) // int(cb.SPLIT_ESTIMATE_SECONDS * cb.NATURAL_WPS))
    assert len(groups) <= fewest + 1
    if len(groups) > 1:
        assert max(est) - min(est) <= 2 * words_each / cb.NATURAL_WPS     # balanced to a sentence


def test_call_groups_fuzz():
    """Random blocks: words conserved and in order, no empty call, every call within the budget when
    whole sentences allow it, one call whenever the block fits."""
    import random

    rng = random.Random(20261022)
    for _ in range(400):
        sents = []
        for _k in range(rng.randint(1, 14)):
            n = rng.choice([1, 2, 3, 8, 15, 30, 60, 130])
            body = " ".join(rng.choice(["alpha", "$4.5 billion", "12%", "2023", "AWS", "—"]) for _w in range(n))
            sents.append(f"Start {body}.")
        text = " ".join(sents)
        groups = cb.call_groups(text)
        assert all(g.strip() for g in groups)
        assert " ".join(groups).split() == text.split()
        if cb.estimate_seconds(text) <= cb.SPLIT_ESTIMATE_SECONDS:
            assert groups == [text]
        for g in groups:
            if len(tm.split_sentences(g)) > 1:
                assert cb.estimate_seconds(g) <= cb.SPLIT_ESTIMATE_SECONDS


def test_call_groups_keeps_a_single_long_sentence_whole():
    one = "This sentence " + " ".join(["keeps going"] * 60) + "."
    assert cb.call_groups(one) == [one]


def test_halves_are_balanced_runs_of_whole_sentences():
    text = "One two three. Four five six seven eight nine. Ten. Eleven twelve thirteen fourteen."
    a, b = cb.halves(text)
    assert (a + " " + b).split() == text.split()
    assert abs(tm.spoken_word_count(a) - tm.spoken_word_count(b)) <= 4
    assert cb.halves("Just one sentence here.") == ["Just one sentence here."]


def _synth_from(lengths: Dict[int, int], calls: List[tuple]):
    def synth(text: str, seed: int):
        calls.append((text, seed))
        return f"audio@{seed}", lengths.get(seed, int(cb.SR * tm.spoken_word_count(text) / 2.7))
    return synth


def test_the_render_loop_rerenders_once_with_seed_plus_1000():
    blocks = [{"i": 0, "text": "One two three four five six."}, {"i": 1, "text": "Seven eight nine ten eleven twelve."}]
    calls: List[tuple] = []
    truncated = int(cb.SR * 39.8)
    out = cb.render_blocks(blocks, _synth_from({cb.SEED + 1: truncated}, calls))
    assert [c[1] for c in calls] == [cb.SEED, cb.SEED + 1, cb.SEED + 1 + cb.RERENDER_SEED_OFFSET]
    assert [b.rerendered for b in out] == [False, True]
    assert out[1].calls[0].seed == cb.SEED + 1 + cb.RERENDER_SEED_OFFSET
    assert out[1].audio == f"audio@{cb.SEED + 1 + cb.RERENDER_SEED_OFFSET}"
    assert "truncated" in out[1].first_reason          # one sentence: re-rendered whole


def test_a_truncated_multi_sentence_call_is_rerendered_as_two_halves():
    text = "The first half of this block is here. The second half follows right after it."
    calls: List[tuple] = []
    out = cb.render_blocks([{"i": 4, "text": text}], _synth_from({cb.SEED + 4: int(cb.SR * 40.0)}, calls))
    retry = cb.SEED + 4 + cb.RERENDER_SEED_OFFSET
    assert [c[1] for c in calls] == [cb.SEED + 4, retry, retry + cb.CALL_SEED_STRIDE]
    assert [c[0] for c in calls[1:]] == cb.halves(text)
    assert len(out[0].calls) == 2 and out[0].rerendered and out[0].words == len(text.split())


def test_a_block_estimated_too_long_is_rendered_in_sentence_groups():
    calls: List[tuple] = []
    out = cb.render_blocks([{"i": 0, "text": _dense()}], _synth_from({}, calls))
    groups = cb.call_groups(_dense())
    assert [c[0] for c in calls] == groups
    assert [c[1] for c in calls] == [cb.SEED + k * cb.CALL_SEED_STRIDE for k in range(len(groups))]
    assert not out[0].rerendered and out[0].spoken_words == sum(tm.spoken_word_count(g) for g in groups)


def test_the_render_loop_fails_a_block_that_fails_twice():
    blocks = [{"i": 0, "text": "Fine words here today."}, {"i": 1, "text": "This block keeps stalling badly."}]
    calls: List[tuple] = []
    slow = int(cb.SR * 30)                   # 5 words in 30 s = 0.17 w/s
    with pytest.raises(cb.BlockGateFailed) as e:
        cb.render_blocks(blocks, _synth_from({cb.SEED + 1: slow, cb.SEED + 1 + cb.RERENDER_SEED_OFFSET: slow}, calls))
    assert e.value.i == 1 and "also on the first render" in e.value.reason
    assert len(calls) == 3


# ══ 4. assembly maths and block windows ══════════════════════════════════════


def test_assemble_plan_matches_the_script():
    """synth_clip (block mode): native wpm over whitespace words and raw speech; atempo =
    TARGET_WPM / native, clamped 0.5-2.0; pauses sized BEFORE the tempo pass; none after the last."""
    lengths = [cb.SR * 10, cb.SR * 20, cb.SR * 5]
    words = [25, 50, 12]
    plan = cb.assemble_plan(lengths, words)
    native = sum(words) / (sum(lengths) / cb.SR) * 60
    atempo = max(0.5, min(2.0, cb.TARGET_WPM / native))
    pause = int(round(cb.BLOCK_PAUSE * atempo * cb.SR))
    assert plan.native_wpm == pytest.approx(native)
    assert plan.atempo == pytest.approx(atempo)
    assert plan.pause_samples == pause
    assert plan.offsets == (0, lengths[0] + pause, lengths[0] + lengths[1] + 2 * pause)
    assert plan.total_samples == sum(lengths) + 2 * pause
    # the pause lands at BLOCK_PAUSE after the tempo pass
    assert cb.final_seconds(pause, plan) == pytest.approx(cb.BLOCK_PAUSE, abs=1 / cb.SR)


@pytest.mark.parametrize("words, seconds, atempo", [(10, 60, 2.0), (1000, 60, 0.5)])
def test_atempo_is_clamped(words, seconds, atempo):
    assert cb.assemble_plan([cb.SR * seconds], [words]).atempo == atempo


def test_a_split_block_gets_sentence_pauses_inside_and_block_pauses_around():
    lengths = [cb.SR * 10, [cb.SR * 20, cb.SR * 15], cb.SR * 5]
    words = [25, 90, 12]
    plan = cb.assemble_plan(lengths, words)
    speech = cb.SR * (10 + 20 + 15 + 5)
    assert plan.native_wpm == pytest.approx(sum(words) / (speech / cb.SR) * 60)   # pauses never count
    inner = int(round(cb.SENT_PAUSE * plan.atempo * cb.SR))
    assert plan.sent_pause_samples == inner
    assert plan.lengths == (cb.SR * 10, cb.SR * 35 + inner, cb.SR * 5)
    assert plan.offsets[2] == cb.SR * 10 + plan.pause_samples + cb.SR * 35 + inner + plan.pause_samples
    assert plan.total_samples == speech + inner + 2 * plan.pause_samples


def test_assemble_plan_of_nothing_is_neutral():
    plan = cb.assemble_plan([], [])
    assert plan.atempo == 1.0 and plan.offsets == () and plan.total_samples == 0


def test_the_encode_is_the_scripts_plus_faststart():
    cmd = cb.encode_command(Path("in.wav"), Path("out.m4a"), 1.0625)
    assert cmd[cmd.index("-filter:a") + 1] == "atempo=1.06250"
    assert cmd[cmd.index("-c:a") + 1] == "aac" and cmd[cmd.index("-b:a") + 1] == "96k"
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert "-ar" not in cmd and "-ac" not in cmd      # 24 kHz mono in, as the script left it
    kcmd = kk.encode_command(Path("in.wav"), Path("out.m4a"))
    assert kcmd[kcmd.index("-b:a") + 1] == "96k" and "+faststart" in kcmd and "-filter:a" not in kcmd


def test_the_block_window_is_raw_offset_over_atempo():
    sr, atempo = cb.SR, 1.25
    lo, hi, core_lo, core_hi = al.block_window(sr * 10, sr * 4, sr=sr, atempo=atempo)
    assert core_lo == pytest.approx(10 / 1.25) and core_hi == pytest.approx(14 / 1.25)
    assert lo == pytest.approx(core_lo - al.WINDOW_MARGIN_SECONDS)
    assert hi == pytest.approx(core_hi + al.WINDOW_MARGIN_SECONDS)
    lo0, _, _, _ = al.block_window(0, sr, sr=sr, atempo=1.0)
    assert lo0 == 0.0                                   # clamped at the start of the file
    _, hi_end, _, _ = al.block_window(sr * 10, sr * 4, sr=sr, atempo=1.0, total=14.1)
    assert hi_end == 14.1                               # clamped at its end
    with pytest.raises(ValueError):
        al.block_window(0, sr, sr=sr, atempo=0)


def test_the_window_margin_stays_inside_the_block_pause():
    """Two neighbours' widened windows never overlap: the margin is under half the pause."""
    assert 2 * al.WINDOW_MARGIN_SECONDS < cb.BLOCK_PAUSE


def test_windows_follow_the_assembly_plan():
    lengths, words = [cb.SR * 12, cb.SR * 30, cb.SR * 2], [30, 80, 3]
    plan = cb.assemble_plan(lengths, words)
    prev_hi = 0.0
    for off, n in zip(plan.offsets, lengths):
        lo, hi, core_lo, core_hi = al.block_window(off, n, sr=cb.SR, atempo=plan.atempo)
        assert core_hi - core_lo == pytest.approx(n / cb.SR / plan.atempo)
        assert core_lo >= prev_hi - 1e-9
        prev_hi = core_hi
    assert prev_hi == pytest.approx(plan.total_samples / cb.SR / plan.atempo)


def test_the_start_error():
    assert al.start_error(10.42, 10.0, 0.5, 1.25) == pytest.approx(0.02)
    assert al.start_error(None, 10.0, 0.5, 1.0) is None


# ══ 5. alignment helpers ═════════════════════════════════════════════════════


@pytest.mark.parametrize("seconds, chunks", [
    (70.0, [(0, 30), (30, 60), (60, 70)]),
    (60.5, [(0, 30), (30, 60.5)]),          # a 0.5 s tail joins the chunk before it
    (30.0, [(0, 30)]),
    (12.0, [(0, 12)]),
    (0.0, []),
])
def test_emission_windows_are_30_seconds(seconds, chunks):
    sr = al.SAMPLE_RATE
    got = al.emission_chunks(int(seconds * sr))
    assert got == [(int(a * sr), int(b * sr)) for a, b in chunks]
    assert al.EMISSION_WINDOW_SECONDS == 30.0


def test_transcript_targets_use_the_spoken_form_and_the_separator():
    words = ["Revenue", "—", "$5B", "rose."]
    targets, slices = al.transcript_targets(words, W2V_DICT)
    sep = W2V_DICT["|"]
    decoded = "".join(W2V_LABELS[t] for t in targets)
    assert decoded == "REVENUE|FIVE|BILLION|DOLLARS|ROSE"
    assert slices[1] is None                            # an em-dash says nothing
    a, b = slices[2]
    assert "".join(W2V_LABELS[t] for t in targets[a:b + 1]) == "FIVE|BILLION|DOLLARS"
    assert targets[a - 1] == sep and targets[0] != sep and targets[-1] != sep


def test_a_word_with_no_letters_at_the_start_adds_no_leading_separator():
    targets, slices = al.transcript_targets(["—", "Yes"], W2V_DICT)
    assert slices == [None, (0, 2)] and "".join(W2V_LABELS[t] for t in targets) == "YES"


def test_ctc_frames_needed_counts_repeats():
    assert al.ctc_frames_needed([5, 5, 6, 7, 7, 7]) == 6 + 3
    assert al.ctc_frames_needed([]) == 0


def test_word_spans_map_frames_to_seconds_and_skip_separators_in_confidence():
    sep = W2V_DICT["|"]
    targets = [3, 4, sep, 5]                            # "TA" | "O"
    token_spans = [(0, 2, 0.9), (2, 4, 0.7), (4, 5, 0.01), (6, 9, 0.5)]
    out = al.word_spans([(0, 1), None, (3, 3)], token_spans, targets, sep=sep, seconds_per_frame=0.02, offset=10.0)
    assert out[0] == [10.0, 10.08, 0.8]
    assert out[1] is None
    assert out[2] == [10.12, 10.18, 0.5]
    with pytest.raises(al.AlignFailed):
        al.word_spans([(0, 0)], token_spans[:2], targets, sep=sep, seconds_per_frame=0.02, offset=0.0)


def test_the_aligner_is_the_mit_bundle_and_mms_fa_is_banned():
    """MMS_FA is CC-BY-NC: never on this path. Identifiers, imports, string constants (docstrings
    excluded, so the ban itself can be explained) and the image's files are all scanned."""
    assert al.MODEL == "WAV2VEC2_ASR_BASE_960H"
    hits = []
    for py in _files():
        tree = _tree(py)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and _is_doc(n.body[0])}
        for node in ast.walk(tree):
            ident = None
            if isinstance(node, ast.Name):
                ident = node.id
            elif isinstance(node, ast.Attribute):
                ident = node.attr
            elif isinstance(node, ast.alias):
                ident = node.name
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                ident = node.value
            if ident and ("MMS_FA" in ident or "_forced_align" in ident or ident.startswith("scripts")):
                hits.append(f"{py.name}: {ident[:60]}")
    assert hits == []
    for name in ("Dockerfile", "requirements.txt", "constraints.txt", "bake.py"):
        text = (PKG / name).read_text()
        assert "MMS_FA" not in text and "mms_fa" not in text.lower(), name


def test_the_mms_fa_scan_would_catch_a_use():
    tree = ast.parse("import torchaudio\nB = torchaudio.pipelines.MMS_FA\n")
    assert any(isinstance(n, ast.Attribute) and n.attr == "MMS_FA" for n in ast.walk(tree))


def test_the_aligner_refuses_downloads():
    src = ast.unparse(_top(_tree(PKG / "align.py"), "load_model"))
    assert "torch.hub.download_url_to_file = _refuse_download" in src
    assert "WAV2VEC2_CHECKPOINT" in src and "weights_missing" in ast.unparse(_tree(PKG / "align.py"))


# ══ 6. timings ═══════════════════════════════════════════════════════════════


def test_the_sentence_split_is_the_scripts():
    """Sentence texts must equal the server's split, which copies scripts/_forced_align.py."""
    script = _tree(SCRIPTS / "_forced_align.py")
    worker = _tree(PKG / "timings.py")
    assert ast.dump(_top(worker, "_SENT")) == ast.dump(_top(script, "_SENT"))
    for fn in ("strip_markup", "split_sentences"):
        assert _fn_dump(_top(worker, fn)) == _fn_dump(_top(script, fn)), fn


@pytest.mark.parametrize("name", ["_SENT", "_SPOKEN_WORD"])
def test_the_split_and_word_count_equal_the_servers(name):
    server = _tree(BACKEND / "app" / "services" / "learn_weekly" / "narration.py")
    assert ast.dump(_top(_tree(PKG / "timings.py"), name)) == ast.dump(_top(server, name))


@pytest.mark.parametrize("name", ["SPW_BAND", "DURATION_SLACK_SECONDS", "MAX_SILENCE_SECONDS", "ORDER_EPSILON_SECONDS"])
def test_the_timing_gate_mirrors_the_servers(name):
    server = _literal(BACKEND / "app" / "services" / "learn_weekly" / "narration.py", name)
    assert getattr(tm, name) == server


def _body(node: ast.AST) -> List[str]:
    return [ast.dump(st) for st in node.body if not _is_doc(st)]


def test_the_servers_markup_strip_and_split_bodies_are_the_same_code():
    """The server's own copies (`strip_markup(s: Any)`, `_split`) differ from the script's only in
    their signatures; their bodies must be the same statements."""
    server = _tree(BACKEND / "app" / "services" / "learn_weekly" / "narration.py")
    worker = _tree(PKG / "timings.py")
    assert _body(_top(server, "strip_markup")) == _body(_top(worker, "strip_markup"))
    assert _body(_top(server, "_split")) == _body(_top(worker, "split_sentences"))


def _catalog_blocks() -> List[str]:
    data = json.loads((BACKEND / "data" / "money_moves.json").read_text(encoding="utf-8"))
    out = []
    for a in data["articles"]:
        out += [a.get("title") or "", a.get("subtitle") or ""]
        for s in a.get("sections", []):
            out.append(s.get("title") or "")
            for blk in s.get("content", []):
                if blk.get("text"):
                    out.append(blk["text"])
                out += blk.get("items") or []
    return [b for b in out if tm.strip_markup(b).strip()]


def test_sentence_ranges_conserve_every_word_of_the_catalog():
    blocks = _catalog_blocks()
    assert len(blocks) > 300
    for text in blocks:
        ranges = tm.sentence_word_ranges(text)
        words = tm.strip_markup(text).split()
        assert [w for _s, a, b in ranges for w in words[a:b]] == words
        assert [s for s, _a, _b in ranges] == tm.sentence_split(text)


@pytest.mark.parametrize("text, spoken", [
    ("In fiscal 2023,", ["in", "fiscal", "twenty", "twenty", "three"]),
    ("$574.8 billion", ["five", "hundred", "seventy", "four", "point", "eight", "billion", "dollars"]),
    ("up 12%", ["up", "twelve", "percent"]),
    ("$5B,", ["five", "billion", "dollars"]),
    ("$1 each", ["one", "dollar", "each"]),
    ("1,525,000 people", ["one", "million", "five", "hundred", "twenty", "five", "thousand", "people"]),
    ("the 1990s", ["the", "nineteen", "nineties"]),
    ("in 2005 and 1900", ["in", "two", "thousand", "five", "and", "nineteen", "hundred"]),
    ("21st century", ["twenty", "first", "century"]),
    ("3.5x revenue", ["three", "point", "five", "times", "revenue"]),
    ("S&P 500 — Q4", ["s", "and", "p", "five", "hundred", "q", "four"]),
    ("the SEC's 10-K", ["the", "sec's", "ten", "k"]),
    ("Don’t panic", ["don't", "panic"]),
])
def test_the_spoken_form(text, spoken):
    words = text.split()
    assert [w for ws in tm.spoken_words_per_word(words) for w in ws] == spoken


def test_a_huge_digit_run_is_read_digit_by_digit():
    assert tm.spoken_words_per_word(["1234567890123456789"])[0] == [tm._ONES[int(c)] for c in "1234567890123456789"]


@pytest.mark.parametrize("name", ["MIN_WORD_SECONDS", "Token", "_PUNCT_ONLY", "_norm", "group_tokens",
                                  "proportional", "align_line", "tokens_from_engine"])
def test_kokoro_timing_code_is_vendored_verbatim(name):
    """marketing/timings.py measured these against kokoro 0.9.4 / misaki 0.9.4."""
    src, dst = _tree(BACKEND / "marketing" / "timings.py"), _tree(PKG / "timings.py")
    a, b = _top(src, name), _top(dst, name)
    if isinstance(a, (ast.FunctionDef, ast.ClassDef)):
        assert _fn_dump(a) == _fn_dump(b)
    else:
        assert ast.dump(a) == ast.dump(b)


def _even_spans(text: str, start: float, spw: float = 0.36) -> List[Optional[tuple]]:
    spans, t = [], start
    for w in tm.strip_markup(text).split():
        n = max(1, len(tm.spoken_words_per_word([w])[0]))
        spans.append((round(t, 3), round(t + spw * n - 0.03, 3)))
        t += spw * n
    return spans


def test_build_block_gives_each_sentence_its_words_span():
    text = "Costco sells cheaply. Members pay a fee! Why? Because it works."
    spans = _even_spans(text, 5.0)
    entry, stats = tm.build_block(3, text, spans, window=(5.0, 30.0))
    assert entry["i"] == 3 and [s["text"] for s in entry["sentences"]] == tm.sentence_split(text)
    assert entry["sentences"][0]["start"] == 5.0
    assert entry["sentences"][1]["start"] == spans[3][0] and entry["sentences"][1]["end"] == spans[6][1]
    assert entry["start"] == entry["sentences"][0]["start"] and entry["end"] == entry["sentences"][-1]["end"]
    assert stats == {"sentences": 4, "covered": 4, "align_coverage": 1.0, "words": 11, "timed_words": 11}


def test_an_untimed_sentence_shares_the_gap_between_its_neighbours():
    text = "First part here. “—”. Last part here."
    spans: List[Optional[tuple]] = [(1.0, 1.3), (1.3, 1.6), (1.6, 2.0), None, (4.0, 4.3), (4.3, 4.6), (4.6, 5.0)]
    entry, stats = tm.build_block(0, text, spans, window=(1.0, 5.0))
    middle = entry["sentences"][1]
    assert middle["text"] == "“—”." and middle["start"] == 2.0 and middle["end"] == 4.0
    assert stats["covered"] == 2 and stats["align_coverage"] == round(2 / 3, 4)


def test_build_block_is_monotonic_and_refuses_a_span_count_mismatch():
    text = "One two. Three four."
    entry, _ = tm.build_block(0, text, [(1.0, 1.5), (1.4, 1.6), (1.55, 1.8), (1.9, 2.2)], window=(0, 3))
    prev = 0.0
    for s in entry["sentences"]:
        assert s["start"] >= prev and s["end"] >= s["start"]
        prev = s["end"]
    with pytest.raises(ValueError):
        tm.build_block(0, text, [(1.0, 1.5)], window=(0, 3))


def _blocks(*texts: str) -> List[dict]:
    return [{"i": i, "text": t, "role": "body"} for i, t in enumerate(texts)]


def _table_for(blocks: List[dict], start: float = 0.3, pause: float = 0.6):
    table, t = [], start
    for b in blocks:
        spans = _even_spans(b["text"], t)
        entry, _ = tm.build_block(b["i"], b["text"], spans, window=(t, spans[-1][1]))
        table.append(entry)
        t = spans[-1][1] + pause
    return tm.enforce_table_order(table), t


def test_check_table_accepts_a_good_table_and_names_each_problem():
    blocks = _blocks("A short title", "The first sentence is here. And a second one follows.")
    table, end = _table_for(blocks)
    assert tm.check_table(blocks, table, end) == []
    bad = json.loads(json.dumps(table))
    bad[1]["sentences"][1]["end"] = bad[1]["sentences"][1]["start"] + 0.05        # a dropped sentence
    assert any("dropped" in p for p in tm.check_table(blocks, bad, end))
    bad = json.loads(json.dumps(table))
    bad[1]["sentences"][0]["text"] = "The first sentence is here"
    assert any("not the server's split" in p for p in tm.check_table(blocks, bad, end))
    assert any("past the audio" in p for p in tm.check_table(blocks, table, end - 3))
    assert any("past the last sentence" in p for p in tm.check_table(blocks, table, end + 10))
    bad = json.loads(json.dumps(table))
    bad[1]["start"] = 0.0
    assert any("before block 0 ends" in p for p in tm.check_table(blocks, bad, end))
    assert tm.check_table(blocks, table[:1], end) == ["1 timing entries for 2 blocks"]
    bad = json.loads(json.dumps(table))
    bad[1]["sentences"][0]["start"] = float("nan")
    assert any("non-finite" in p for p in tm.check_table(blocks, bad, end))


@pytest.mark.parametrize("engine", ["chatterbox", "kokoro"])
def test_the_bench_fixture_builds_a_table_the_server_accepts(engine):
    """The whole fixture — the worst cases included — through the worker's own table builder, from a
    plausible render (0.36 s per spoken word), passes the server's checks."""
    fx = bench.load_fixture()
    job = bench.fixture_job(fx, engine, None)
    raw = {"engine": engine, "blocks": job.blocks}
    render, aligned, total = _synthetic(raw)
    table, checks = m.build_timings(job, render, aligned if engine == "chatterbox" else None)
    assert tm.check_table(job.blocks, table, total) == []
    assert all(c["align_coverage"] == 1.0 for c in checks)


def test_check_table_flags_a_long_silence_between_sentences():
    blocks = _blocks("Here we go now.", "Much later this one starts.")
    table, end = _table_for(blocks, pause=8.0)
    assert any("covered by no sentence" in p for p in tm.check_table(blocks, table, end))


# ══ 7. verify ════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name", ["SR", "F0_HZ", "WPM", "BYTES_PER_SEC", "LUFS"])
def test_the_bands_equal_verify_learn_voice(name):
    assert _literal(PKG / "verify.py", name) == _literal(SCRIPTS / "verify_learn_voice.py", name) == getattr(verify, name)


@pytest.mark.parametrize("name", ["_run", "duration", "lufs", "median_f0"])
def test_the_measuring_functions_equal_verify_learn_voice(name):
    """median_f0 differs by ONE statement: the script imports numpy at module level, the worker
    inside the function (its parent process stays free of the heavy stack)."""
    numpy_import = ast.dump(ast.parse("import numpy as np").body[0])
    worker = _fn_dump(_top(_tree(PKG / "verify.py"), name), drop=(numpy_import,))
    script = _fn_dump(_top(_tree(SCRIPTS / "verify_learn_voice.py"), name))
    assert worker == script


def test_the_chatterbox_bands_are_the_catalogs_and_kokoro_pitch_is_unenforced():
    assert verify.BANDS["chatterbox"] == {"f0_hz": verify.F0_HZ, "wpm": verify.WPM,
                                          "bytes_per_sec": verify.BYTES_PER_SEC, "lufs": verify.LUFS}
    assert verify.BANDS["kokoro"]["f0_hz"] is None


def test_band_problems():
    good = {"duration": 600.0, "f0_hz": 134.0, "wpm": 158.0, "bytes_per_sec": 12_100, "lufs": -16.0}
    assert verify.band_problems("chatterbox", good) == []
    assert verify.band_problems("chatterbox", {**good, "f0_hz": 125.0}) == ["f0_hz 125.0 outside (130.0, 142.0)"]
    assert verify.band_problems("chatterbox", {**good, "lufs": None}) == ["lufs could not be measured"]
    assert verify.band_problems("chatterbox", {**good, "duration": 0}) == ["the file has no readable duration"]
    assert verify.band_problems("kokoro", {**good, "f0_hz": None}) == []
    assert verify.band_problems("nope", good)


def test_the_web_bytes_band_agrees_with_the_workers():
    """The server refuses a file outside BYTES_PER_SECOND_BAND (keyed by `c.ENGINE_*` names, so it is
    not a literal: its two tuples are read from the dict's values)."""
    band = _top(_tree(BACKEND / "app" / "services" / "learn_weekly" / "narration.py"), "BYTES_PER_SECOND_BAND")
    keys = [ast.unparse(k) for k in band.keys]
    values = dict(zip(keys, (ast.literal_eval(v) for v in band.values)))
    assert values == {"c.ENGINE_CHATTERBOX": (11_500.0, 12_800.0), "c.ENGINE_KOKORO": (10_000.0, 14_000.0)}
    assert tuple(verify.BANDS["chatterbox"]["bytes_per_sec"]) == values["c.ENGINE_CHATTERBOX"]
    assert tuple(verify.BANDS["kokoro"]["bytes_per_sec"]) == values["c.ENGINE_KOKORO"]


# ══ 8. threads and the child runner ══════════════════════════════════════════


@pytest.mark.parametrize("env, cpu_max, want", [
    ({"LEARN_NARRATOR_THREADS": "3"}, "400000 100000", 3),
    ({"LEARN_NARRATOR_THREADS": "zero"}, "400000 100000", 4),
    ({"LEARN_NARRATOR_THREADS": "0"}, "400000 100000", 4),
    ({}, "400000 100000", 4),
    ({}, "150000 100000", 2),            # 1.5 CPUs → rounded up
    ({}, "50000 100000", 1),
    ({}, "max 100000", runtime.DEFAULT_THREADS),
    ({}, "garbage", runtime.DEFAULT_THREADS),
    ({}, "0 100000", runtime.DEFAULT_THREADS),
    ({}, None, runtime.DEFAULT_THREADS),  # no cgroup file (not Linux)
])
def test_threads_come_from_the_cgroup_quota(tmp_path, env, cpu_max, want):
    path = tmp_path / "cpu.max"
    if cpu_max is not None:
        path.write_text(cpu_max + "\n")
    assert runtime.torch_threads(env, cpu_max=path) == want


def test_the_child_env_caps_threads_and_forces_offline():
    env = runtime.child_env(4, base={"HF_HUB_OFFLINE": "0", "PATH": "/bin"})
    assert env["OMP_NUM_THREADS"] == env["MKL_NUM_THREADS"] == env["LEARN_NARRATOR_THREADS"] == "4"
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["PATH"] == "/bin"


class FakeProc:
    def __init__(self, *, polls: int, code: int, on_exit=None):
        self.polls_left, self.code, self.on_exit = polls, code, on_exit
        self.returncode: Optional[int] = None
        self.killed = False

    def wait(self, timeout=None):
        if self.killed:
            self.returncode = -9
            return -9
        if self.polls_left > 0:
            self.polls_left -= 1
            raise subprocess.TimeoutExpired("child", timeout)
        if self.on_exit:
            self.on_exit()
        self.returncode = self.code
        return self.code

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True


def _clock(step: float):
    t = {"now": 0.0}

    def clock():
        t["now"] += step
        return t["now"]
    return clock


def _run(tmp_path, proc: FakeProc, *, timeout=1000.0, heartbeat=None, step=1.0, beat_every=10.0):
    job = tmp_path / "job.json"
    job.write_text("{}")
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return proc
    result = runtime.run_child("render", job, tmp_path, timeout=timeout, threads=2, heartbeat=heartbeat,
                               popen=popen, clock=_clock(step), poll=0.0, beat_every=beat_every)
    return result, seen


def test_run_child_returns_the_result_and_heartbeats(tmp_path):
    beats = []
    proc = FakeProc(polls=6, code=0, on_exit=lambda: (tmp_path / "render.json").write_text('{"ok": 1}'))
    result, seen = _run(tmp_path, proc, heartbeat=lambda: beats.append(1), step=5.0)
    assert result == {"ok": 1}
    assert seen["cmd"][1:4] == ["-m", "learn_narrator.child", "render"]
    assert seen["env"]["HF_HUB_OFFLINE"] == "1" and seen["env"]["OMP_NUM_THREADS"] == "2"
    assert len(beats) >= 2


def test_run_child_kills_on_timeout(tmp_path):
    proc = FakeProc(polls=10 ** 6, code=0)
    with pytest.raises(runtime.ChildFailed) as e:
        _run(tmp_path, proc, timeout=50.0, step=20.0)
    assert e.value.code == "render_timeout" and proc.killed


def test_run_child_names_an_oom_kill(tmp_path):
    with pytest.raises(runtime.ChildFailed) as e:
        _run(tmp_path, FakeProc(polls=0, code=-9))
    assert e.value.code == "render_oom"


def test_run_child_reports_the_childs_typed_failure(tmp_path):
    def typed():
        (tmp_path / "error.json").write_text(json.dumps({"code": "block_gate", "detail": "block 7: truncated"}))
    with pytest.raises(runtime.ChildFailed) as e:
        _run(tmp_path, FakeProc(polls=0, code=runtime.CHILD_TYPED_FAILURE, on_exit=typed))
    assert (e.value.code, e.value.detail) == ("block_gate", "block 7: truncated")


def test_run_child_reports_a_crash_with_the_log_tail(tmp_path):
    def crash():
        (tmp_path / "render.log").write_text("Traceback ...\nRuntimeError: boom\n")
    with pytest.raises(runtime.ChildFailed) as e:
        _run(tmp_path, FakeProc(polls=0, code=1, on_exit=crash))
    assert e.value.code == "render_failed" and "boom" in e.value.detail


def test_a_heartbeat_that_raises_kills_the_child_and_propagates(tmp_path):
    proc = FakeProc(polls=10 ** 6, code=0)

    def lost():
        raise m.JobLost("409")
    with pytest.raises(m.JobLost):
        _run(tmp_path, proc, heartbeat=lost, step=20.0)
    assert proc.killed


def test_a_zero_exit_without_a_result_is_a_failure(tmp_path):
    with pytest.raises(runtime.ChildFailed) as e:
        _run(tmp_path, FakeProc(polls=0, code=0))
    assert e.value.code == "render_failed"


# ══ 9. the HTTP client's status table ════════════════════════════════════════


class Server:
    """A fake internal API: a queue of (status, json body) answers per path; records requests."""

    def __init__(self, answers: Dict[str, List[Any]]):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = request.url.path.split("/api/v1/internal/learn")[-1] if "internal/learn" in request.url.path else "PUT"
        queue = self.answers.get(key) or self.answers.get("*")
        ans = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(ans, Exception):
            raise ans
        status, body = ans
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)


def _client(server: Server, sleeps: Optional[list] = None) -> api.NarratorClient:
    return api.NarratorClient("https://caydex.test", "tok-123", transport=httpx.MockTransport(server),
                              sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


def test_claim_204_is_none_and_200_is_the_job():
    server = Server({"/jobs/claim": [(204, None), (200, {"job": {"id": "j1"}})]})
    c = _client(server)
    assert c.claim(["chatterbox"], "v1") is None
    assert c.claim(["chatterbox"], "v1") == {"id": "j1"}
    req = server.requests[0]
    assert req.headers[api.TOKEN_HEADER] == "tok-123" and api.CLAIM_HEADER not in req.headers
    assert json.loads(req.content) == {"engines": ["chatterbox"], "worker_version": "v1"}


def test_hold_presents_the_claim_on_every_later_call():
    server = Server({"*": [(200, {"ok": True})]})
    c = _client(server)
    c.hold(3, "abcdef0123456789")
    c.heartbeat("j1")
    assert server.requests[0].headers[api.CLAIM_HEADER] == "3.abcdef0123456789"
    assert server.requests[0].url.path == "/api/v1/internal/learn/jobs/j1/heartbeat"
    assert json.loads(server.requests[0].content) == {}


@pytest.mark.parametrize("status, cls", [(401, api.NarratorAuthError), (403, api.NarratorAuthError),
                                         (404, api.NarratorTerminalError), (409, api.NarratorTerminalError),
                                         (422, api.NarratorTerminalError), (500, api.NarratorAPIError),
                                         (400, api.NarratorAPIError)])
def test_non_retryable_statuses_are_not_retried(status, cls):
    body = {"error_code": "LEARN_NARRATOR_JOB_NOT_HELD", "message": "the claim no longer holds", "user_message": "x"}
    server = Server({"*": [(status, body)]})
    sleeps: list = []
    with pytest.raises(cls) as e:
        _client(server, sleeps).heartbeat("j1")
    assert len(server.requests) == 1 and sleeps == []
    assert e.value.status == status and e.value.error_code == "LEARN_NARRATOR_JOB_NOT_HELD"
    assert type(e.value) is cls


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_errors_are_retried_three_times_with_backoff(status):
    server = Server({"*": [(status, None)]})
    sleeps: list = []
    with pytest.raises(api.NarratorUnavailable):
        _client(server, sleeps).complete("j1", {"x": 1})
    assert len(server.requests) == 4 and sleeps == list(api.BACKOFF_SECONDS)


def test_a_retry_that_succeeds_returns_the_body():
    server = Server({"*": [(503, None), (200, {"status": "ready"})]})
    assert _client(server).complete("j1", {}) == {"status": "ready"}
    assert len(server.requests) == 2


def test_transport_errors_are_retried():
    server = Server({"*": [httpx.ConnectError("refused"), (200, {"ok": True})]})
    assert _client(server).heartbeat("j1") == {"ok": True}
    assert len(server.requests) == 2


def test_a_nested_detail_error_body_is_read():
    server = Server({"*": [(409, {"detail": {"error_code": "LEARN_NARRATOR_JOB_NOT_HELD", "message": "gone"}})]})
    with pytest.raises(api.NarratorTerminalError) as e:
        _client(server).heartbeat("j1")
    assert e.value.error_code == "LEARN_NARRATOR_JOB_NOT_HELD" and "gone" in str(e.value)


def test_an_empty_token_is_refused_before_any_call():
    with pytest.raises(api.NarratorAuthError):
        api.NarratorClient("https://x.test", "")


SIGNED = "https://proj.supabase.co/storage/v1/object/upload/sign/money-moves-media/audio/weekly/x-abc.m4a?token=SECRET-WRITE-TOKEN"


def test_put_signed_sends_raw_bytes_with_the_contract_headers(caplog):
    server = Server({"PUT": [(200, {"Key": "x"})]})
    with caplog.at_level(logging.DEBUG):
        _client(server).put_signed(SIGNED, b"\x00audio", path="audio/weekly/x-abc.m4a", apikey="sb_publishable_x")
    req = server.requests[0]
    assert req.method == "PUT" and req.content == b"\x00audio"
    assert req.headers["content-type"] == "audio/mp4" and req.headers["x-upsert"] == "false"
    assert req.headers["apikey"] == "sb_publishable_x" and api.TOKEN_HEADER not in req.headers
    assert "SECRET-WRITE-TOKEN" not in caplog.text


@pytest.mark.parametrize("answers, ok", [([(409, {"error": "Duplicate"})], True),
                                         ([(503, None), (200, {})], True),
                                         ([(400, {"error": "bad"})], False)])
def test_put_signed_status_handling(answers, ok, caplog):
    server = Server({"PUT": answers})
    with caplog.at_level(logging.DEBUG):
        if ok:
            _client(server).put_signed(SIGNED, b"x", path="audio/weekly/x-abc.m4a")
        else:
            with pytest.raises(api.NarratorAPIError) as e:
                _client(server).put_signed(SIGNED, b"x", path="audio/weekly/x-abc.m4a")
            assert "SECRET-WRITE-TOKEN" not in str(e.value)
    assert "SECRET-WRITE-TOKEN" not in caplog.text


def test_put_signed_transport_errors_never_carry_the_url(caplog):
    server = Server({"PUT": [httpx.ConnectError(f"cannot reach {SIGNED}")]})
    with caplog.at_level(logging.DEBUG), pytest.raises(api.NarratorUnavailable) as e:
        _client(server).put_signed(SIGNED, b"x", path="p.m4a")
    assert "SECRET-WRITE-TOKEN" not in caplog.text and "SECRET-WRITE-TOKEN" not in str(e.value)


# A redirect is never "no job" and never "stored" (2026-10-23). httpx does not follow redirects, and
# before this an empty-bodied 3xx on claim returned None — the worker logged "no narration job"
# forever behind a redirecting LEARN_NARRATOR_API_BASE (http:// for https://, a bare domain → www).

REDIRECTS = [300, 301, 302, 303, 304, 307, 308]


def _redirecting(status: int, location: Optional[str], *, json_body: Any = None):
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers = {"location": location} if location is not None else {}
        if json_body is not None:
            return httpx.Response(status, headers=headers, json=json_body)
        return httpx.Response(status, headers=headers)
    return seen, handler


def _redirect_client(handler, sleeps: list) -> api.NarratorClient:
    return api.NarratorClient("http://caydex.test", "tok-123", transport=httpx.MockTransport(handler),
                              sleep=sleeps.append)


@pytest.mark.parametrize("status", REDIRECTS)
def test_a_redirected_claim_is_an_error_never_no_job(status):
    seen, handler = _redirecting(status, "https://www.caydex.test/api/v1/internal/learn/jobs/claim?probe=PATH-SECRET")
    sleeps: list = []
    with pytest.raises(api.NarratorRedirectError) as e:
        _redirect_client(handler, sleeps).claim(["chatterbox"], "v1")
    assert len(seen) == 1 and sleeps == []                     # never followed, never retried
    assert e.value.status == status and isinstance(e.value, api.NarratorAPIError)
    assert "to https://www.caydex.test" in str(e.value) and "LEARN_NARRATOR_API_BASE" in str(e.value)
    assert "probe=" not in str(e.value) and "PATH-SECRET" not in str(e.value)   # host only


@pytest.mark.parametrize("route", ["heartbeat", "upload_target", "complete", "fail"])
@pytest.mark.parametrize("status", [301, 307])
def test_every_json_route_refuses_a_redirect_even_with_a_json_body(route, status):
    seen, handler = _redirecting(status, "/elsewhere", json_body={"ok": True, "path": "p", "status": "ready"})
    c = _redirect_client(handler, [])
    call = {"heartbeat": lambda: c.heartbeat("j1"),
            "upload_target": lambda: c.upload_target("j1", size_bytes=1, sha256="a" * 64),
            "complete": lambda: c.complete("j1", {}),
            "fail": lambda: c.fail("j1", "render_failed", "x")}[route]
    with pytest.raises(api.NarratorRedirectError) as e:
        call()
    assert len(seen) == 1 and "to http://caydex.test" in str(e.value)     # a relative Location resolves


@pytest.mark.parametrize("location", [None, "", "not a url at all"])
def test_a_redirect_without_a_usable_location_still_fails_loudly(location):
    seen, handler = _redirecting(302, location)
    with pytest.raises(api.NarratorRedirectError) as e:
        _redirect_client(handler, []).claim(["kokoro"], "v1")
    assert len(seen) == 1 and "302 redirect" in str(e.value)


#: Locations httpx itself chokes on: for 301/302/303/307/308 it parses Location INSIDE request(),
#: so without the response hook these were a retried RemoteProtocolError ("http://[::1"), an
#: untyped InvalidURL ("x:y", "https:x/…") or an IDNAError ("http://xn--/") that crashed main().
MALFORMED_LOCATIONS = ["http://[::1", "x:y", "https:x/y?token=SECRET-WRITE-TOKEN", "http://xn--/"]


@pytest.mark.parametrize("status", [300, 301, 302, 304, 307, 308])
@pytest.mark.parametrize("location", MALFORMED_LOCATIONS)
def test_a_malformed_location_is_one_typed_unretried_redirect_error(status, location):
    seen, handler = _redirecting(status, location)
    sleeps: list = []
    with pytest.raises(api.NarratorRedirectError) as e:
        _redirect_client(handler, sleeps).claim(["kokoro"], "v1")
    assert len(seen) == 1 and sleeps == [] and e.value.status == status
    assert "SECRET-WRITE-TOKEN" not in str(e.value)


@pytest.mark.parametrize("status", [301, 302, 307])
@pytest.mark.parametrize("location", MALFORMED_LOCATIONS)
def test_put_signed_refuses_a_malformed_redirect_once_without_leaking(status, location, caplog):
    seen, handler = _redirecting(status, location)
    sleeps: list = []
    with caplog.at_level(logging.DEBUG), pytest.raises(api.NarratorRedirectError) as e:
        _redirect_client(handler, sleeps).put_signed(SIGNED, b"x" * 1024, path="audio/weekly/x-abc.m4a")
    assert len(seen) == 1 and sleeps == []                     # the ~9 MB body is never re-sent
    for text in (str(e.value), caplog.text):
        assert "SECRET-WRITE-TOKEN" not in text


def test_the_redirect_target_keeps_a_non_default_port_but_never_userinfo():
    seen, handler = _redirecting(301, "https://user:pw@www.caydex.test:8443/api/v1/internal/learn/jobs/claim?q=1")
    with pytest.raises(api.NarratorRedirectError) as e:
        _redirect_client(handler, []).claim(["kokoro"], "v1")
    assert "to https://www.caydex.test:8443 " in str(e.value)
    assert "user" not in str(e.value) and "pw@" not in str(e.value) and "q=1" not in str(e.value)


@pytest.mark.parametrize("location", ["https://www.caydex.test/", *MALFORMED_LOCATIONS])
def test_main_with_the_real_client_exits_2_on_any_redirected_claim(tmp_path, monkeypatch, caplog, location):
    env = {**_bake(tmp_path / "models", voices=("am_michael",)), "LEARN_NARRATOR_API_BASE": "http://caydex.test",
           "LEARN_NARRATOR_API_TOKEN": "tok", "LEARN_NARRATOR_THREADS": "2"}
    monkeypatch.setattr(m.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen, handler = _redirecting(301, location)

    def factory(base, token):
        return api.NarratorClient(base, token, transport=httpx.MockTransport(handler), sleep=lambda s: None)

    def no_render(*a, **k):
        raise AssertionError("nothing may render after a redirected claim")

    with caplog.at_level(logging.INFO):
        code = m.main([], env=env, client_factory=factory, runner=no_render)
    assert code == m.EXIT_CONFIG == 2 and len(seen) == 1
    assert "claim redirected" in caplog.text and "no narration job" not in caplog.text


@pytest.mark.parametrize("status", REDIRECTS)
def test_put_signed_refuses_a_redirect_and_never_names_its_target(status, caplog):
    seen, handler = _redirecting(status, SIGNED.replace("proj.supabase.co", "evil.example") + "&again=SECRET-WRITE-TOKEN")
    c = _redirect_client(handler, [])
    with caplog.at_level(logging.DEBUG), pytest.raises(api.NarratorRedirectError) as e:
        c.put_signed(SIGNED, b"x", path="audio/weekly/x-abc.m4a")
    assert len(seen) == 1 and e.value.status == status
    for text in (str(e.value), caplog.text):
        assert "SECRET-WRITE-TOKEN" not in text and "evil.example" not in text and "supabase.co" not in text
    assert "audio/weekly/x-abc.m4a" in str(e.value)


def test_a_redirected_claim_exits_2_as_a_configuration_error(tmp_path, monkeypatch, caplog):
    redirect = api.NarratorRedirectError("POST /jobs/claim -> 301 redirect to https://www.caydex.test", status=301)
    with caplog.at_level(logging.INFO):          # INFO: the "no narration job" line logs at INFO
        code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), claim=redirect)
    assert code == m.EXIT_CONFIG == 2 and fake.names() == ["claim", "close"]
    assert "LEARN_NARRATOR_API_BASE" in caplog.text and "no narration job" not in caplog.text


def test_a_redirected_complete_is_reported_never_completed(tmp_path, monkeypatch, caplog):
    """Before the fix an empty-bodied 3xx on /complete returned {} — the worker logged
    'narration COMPLETE' and exited 0 with nothing recorded."""
    with caplog.at_level(logging.INFO):
        code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(),
                              complete=api.NarratorRedirectError("POST /jobs/j/complete -> 307 redirect", status=307))
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == "api_error" and "narration COMPLETE" not in caplog.text


def test_a_redirected_upload_is_reported_as_upload_failed(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(),
                          put=api.NarratorRedirectError("signed upload PUT p -> 302 redirect (not followed)", status=302))
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == "upload_failed" and "complete" not in fake.names()


# ══ 10. the whole tick ═══════════════════════════════════════════════════════


def _bake(root: Path, *, chatterbox=True, kokoro=True, wav2vec=True, voices=("am_michael",)) -> Dict[str, str]:
    hf, th = root / "hf", root / "torch"

    def repo(name: str, files):
        base = hf / "hub" / ("models--" + name.replace("/", "--"))
        snap = base / "snapshots" / "c0ffee"
        for f in files:
            (snap / f).parent.mkdir(parents=True, exist_ok=True)
            (snap / f).write_bytes(b"w")
        (base / "refs").mkdir(parents=True, exist_ok=True)
        (base / "refs" / "main").write_text("c0ffee")
    if chatterbox:
        repo(runtime.CHATTERBOX_REPO, runtime.CHATTERBOX_FILES)
    if kokoro:
        repo(runtime.KOKORO_REPO, list(runtime.KOKORO_FILES) + [f"voices/{v}.pt" for v in voices])
    if wav2vec:
        (th / "hub" / "checkpoints").mkdir(parents=True, exist_ok=True)
        (th / "hub" / "checkpoints" / runtime.WAV2VEC2_CHECKPOINT).write_bytes(b"w")
    return {"HF_HOME": str(hf), "TORCH_HOME": str(th)}


def test_missing_weights_reads_refs_main(tmp_path):
    env = _bake(tmp_path)
    assert runtime.missing_weights("chatterbox", env=env) == []
    assert runtime.missing_weights("kokoro", voice="am_michael", env=env) == []
    assert runtime.missing_weights("kokoro", voice="bm_george", env=env) == [f"{runtime.KOKORO_REPO}/voices/bm_george.pt"]
    assert runtime.baked_kokoro_voices(env) == ["am_michael"]
    (Path(env["HF_HOME"]) / "hub" / "models--ResembleAI--chatterbox" / "refs" / "main").unlink()
    assert "no refs/main" in runtime.missing_weights("chatterbox", env=env)[0]
    env2 = _bake(tmp_path / "b", wav2vec=False)
    assert runtime.missing_weights("chatterbox", env=env2) == [f"torch hub {runtime.WAV2VEC2_CHECKPOINT}"]


JOB_TEXTS = ["Why Moats Matter", "A moat keeps rivals out. It lets a business earn more for longer.",
             "Scale is one kind of moat.", "Brands are another! Customers return because they trust the name."]


def _raw_job(engine="chatterbox", **over) -> Dict[str, Any]:
    job = {"id": "6f1c0a2e-0000-4000-8000-000000000001", "round": 1, "engine": engine,
           "voice": ({"exaggeration": 0.65, "cfg_weight": 0.4, "target_wpm": 165, "ref": "caydex_voice_achird_v2"}
                     if engine == "chatterbox" else {"voice": "am_michael"}),
           "pipeline_version": "learn-narration/v1", "attempts": 2, "claim_nonce": "0123456789abcdef",
           "blocks": [{"i": i, "text": t, "role": "context" if i == 0 else "body"} for i, t in enumerate(JOB_TEXTS)]}
    job.update(over)
    return job


def _synthetic(job: Dict[str, Any], *, collapse: bool = False):
    """A plausible render + alignment of `job`: 0.36 s per spoken word, 0.6 s pauses, atempo 1."""
    sr, pause = cb.SR, int(cb.BLOCK_PAUSE * cb.SR)
    rblocks, ablocks, off = [], [], 0
    for b in job["blocks"]:
        spoken = tm.spoken_word_count(b["text"])
        n = int(sr * (spoken * 0.36 + 0.2))
        start = off / sr + 0.1
        spans = _even_spans(b["text"], start)
        if collapse and b["i"] == 1:
            spans = [(start, start + 0.01)] * len(spans)          # every word squeezed: dropped audio
        rblocks.append({"i": b["i"], "words": len(b["text"].split()), "spoken_words": spoken, "raw_samples": n,
                        "raw_offset": off, "raw_seconds": round(n / sr, 3), "words_per_sec": round(spoken / (n / sr), 3),
                        "rerendered": b["i"] == 2, "first_reason": "x" if b["i"] == 2 else None, "seed": 1,
                        "lead_silence": 0.1, "word_spans": [list(s) for s in spans], "exact": True,
                        "align_coverage": 1.0})
        ablocks.append({"i": b["i"], "words": [[s[0], s[1], 0.9] for s in spans], "confidence": 0.9,
                        "word_coverage": 1.0, "window": [0, 0], "core": [0, 0]})
        off += n + pause
    render = {"engine": job["engine"], "pipeline": "test", "sample_rate": sr, "atempo": 1.0, "audio": "narration.m4a",
              "emotion_adv": 0.65, "wall_seconds": 12.0, "blocks": rblocks}
    return render, {"model": al.MODEL, "wall_seconds": 3.0, "blocks": ablocks}, off / sr


class FakeAPI:
    def __init__(self, job: Optional[dict], **over):
        self.job, self.calls = job, []
        self.over = over

    def _maybe(self, name):
        exc = self.over.get(name)
        if exc is not None:
            raise exc

    def claim(self, engines, worker_version):
        self.calls.append(("claim", engines, worker_version))
        self._maybe("claim")
        return self.job

    def hold(self, attempts, nonce):
        self.calls.append(("hold", attempts, nonce))

    def heartbeat(self, job_id):
        self.calls.append(("heartbeat", job_id))
        self._maybe("heartbeat")
        return {"ok": True}

    def upload_target(self, job_id, *, size_bytes, sha256):
        self.calls.append(("upload", job_id, size_bytes, sha256))
        self._maybe("upload")
        return self.over.get("upload_body") or {"upload_url": SIGNED, "path": "audio/weekly/x-0123456789ab.m4a"}

    def put_signed(self, url, data, *, path, apikey=None):
        self.calls.append(("put", path, len(data), apikey))
        self._maybe("put")

    def complete(self, job_id, payload):
        self.calls.append(("complete", job_id, payload))
        self._maybe("complete")
        return {"ok": True, "status": "ready"}

    def fail(self, job_id, code, detail):
        self.calls.append(("fail", job_id, code, detail))
        return {"ok": True, "status": "queued", "worker_failures": 1}

    def close(self):
        self.calls.append(("close",))

    def names(self):
        return [c[0] for c in self.calls]


def _tick(tmp_path, monkeypatch, job: Optional[dict], *, collapse=False, measure=None, render_exc=None,
          env_over=None, **api_over):
    env = {**_bake(tmp_path / "models", voices=("am_michael",)), "LEARN_NARRATOR_API_BASE": "https://caydex.test",
           "LEARN_NARRATOR_API_TOKEN": "tok", "LEARN_NARRATOR_THREADS": "2", **(env_over or {})}
    monkeypatch.setattr(m.shutil, "which", lambda name: f"/usr/bin/{name}")
    fake = FakeAPI(job, **api_over)
    seen: Dict[str, Any] = {"modes": []}
    if job is not None:
        render, aligned, total = _synthetic(job, collapse=collapse)
    else:
        render = aligned = total = None

    def runner(mode, job_path, out_dir, *, timeout, threads, heartbeat):
        seen["modes"].append(mode)
        seen["child_job"] = json.loads(job_path.read_text())
        heartbeat()
        if render_exc is not None:
            raise render_exc
        if mode == "render":
            (out_dir / "narration.m4a").write_bytes(b"\x00" * 4096)
            return render
        return aligned

    def fake_measure(path, words):
        seen["words"] = words
        return measure or {"duration": round(total, 3), "f0_hz": 134.5, "wpm": 157.0, "bytes_per_sec": 12_050,
                           "lufs": -16.1}
    monkeypatch.setattr(m, "process", _with_measure(m.process, fake_measure))
    code = m.main([], env=env, client_factory=lambda b, t: fake, runner=runner)
    return code, fake, seen


def _with_measure(process, measure):
    def wrapped(*a, **k):
        k["measure"] = measure
        return process(*a, **k)
    return wrapped


def test_a_good_tick_uploads_and_completes(tmp_path, monkeypatch):
    code, fake, seen = _tick(tmp_path, monkeypatch, _raw_job())
    assert code == 0
    assert fake.names()[:2] == ["claim", "hold"] and fake.calls[1][1:] == (2, "0123456789abcdef")
    assert seen["modes"] == ["render_part", "render", "align"]      # 4 blocks: one part
    assert "fail" not in fake.names() and fake.names()[-1] == "close"
    assert seen["child_job"]["seed"] == cb.SEED and seen["child_job"]["engine"] == "chatterbox"
    put = next(c for c in fake.calls if c[0] == "put")
    assert put[1] == "audio/weekly/x-0123456789ab.m4a" and put[2] == 4096
    upload = next(c for c in fake.calls if c[0] == "upload")
    assert upload[2] == 4096 and upload[3] == hashlib.sha256(b"\x00" * 4096).hexdigest()
    payload = next(c for c in fake.calls if c[0] == "complete")[2]
    assert set(payload) == {"path", "bytes", "duration_seconds", "engine", "pipeline_version", "blocks", "checks"}
    assert payload["engine"] == "chatterbox" and payload["pipeline_version"] == "learn-narration/v1"
    assert [b["i"] for b in payload["blocks"]] == [0, 1, 2, 3]
    assert [s["text"] for s in payload["blocks"][1]["sentences"]] == tm.sentence_split(JOB_TEXTS[1])
    for b in payload["checks"]["blocks"]:
        assert {"i", "raw_seconds", "words_per_sec", "align_coverage", "rerendered"} <= set(b)
    assert payload["checks"]["blocks"][2]["rerendered"] is True
    assert set(payload["checks"]["file"]) >= {"f0_hz", "wpm", "bytes_per_sec", "lufs"}
    assert seen["words"] == sum(len(t.split()) for t in JOB_TEXTS)
    assert payload["checks"]["worker"]["emotion_adv"] == 0.65
    json.dumps(payload, allow_nan=False)   # valid JSON: no NaN anywhere


def test_kokoro_needs_no_alignment_child(tmp_path, monkeypatch):
    code, fake, seen = _tick(tmp_path, monkeypatch, _raw_job("kokoro"))
    assert code == 0 and seen["modes"] == ["render"]
    payload = next(c for c in fake.calls if c[0] == "complete")[2]
    assert payload["engine"] == "kokoro" and payload["checks"]["blocks"][0]["engine_coverage"] == 1.0


def test_no_job_is_a_quiet_exit(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, None)
    assert code == 0 and fake.names() == ["claim", "close"]
    assert fake.calls[0][1] == ["chatterbox", "kokoro"]


def test_a_409_heartbeat_stops_cleanly_without_reporting(tmp_path, monkeypatch, caplog):
    lost = api.NarratorTerminalError("409", status=409, error_code="LEARN_NARRATOR_JOB_NOT_HELD")
    with caplog.at_level(logging.WARNING):
        code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), heartbeat=lost)
    assert code == 0
    assert not {"fail", "upload", "complete"} & set(fake.names())
    assert "no longer ours" in caplog.text


def test_a_transient_heartbeat_failure_does_not_stop_the_job(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), heartbeat=api.NarratorUnavailable("503"))
    assert code == 0 and "complete" in fake.names()


def test_a_storage_refusal_is_an_upload_failure(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(),
                          put=api.NarratorAPIError("signed upload PUT audio/weekly/x.m4a -> 400: bad", status=400))
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == "upload_failed" and "complete" not in fake.names()


def test_an_api_outage_at_complete_does_not_spend_a_worker_failure(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), complete=api.NarratorUnavailable("503 x4"))
    assert code == 1 and "fail" not in fake.names()


def test_a_server_bug_at_complete_is_reported(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), complete=api.NarratorAPIError("500", status=500))
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == "api_error"


def test_an_unexpected_bug_still_reaches_fail(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), render_exc=KeyError("raw_offset"))
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == "worker_error" and "KeyError" in fail[3]


def test_an_auth_failure_mid_job_exits_2(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), heartbeat=api.NarratorAuthError("401", status=401))
    assert code == 2 and "complete" not in fake.names()


def test_an_auth_failure_on_claim_exits_2(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), claim=api.NarratorAuthError("403", status=403))
    assert code == 2 and fake.names() == ["claim", "close"]


def test_a_render_failure_is_reported_once(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(),
                          render_exc=runtime.ChildFailed("block_gate", "block 3: truncated: 39.80s raw"))
    assert code == 1
    fails = [c for c in fake.calls if c[0] == "fail"]
    assert len(fails) == 1 and fails[0][2] == "block_gate" and "block 3" in fails[0][3]
    assert "upload" not in fake.names()


def test_a_dropped_sentence_fails_before_the_upload(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), collapse=True)
    assert code == 1
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert fail[2] == "timings_invalid" and "dropped" in fail[3]
    assert "upload" not in fake.names()


def test_a_voice_outside_the_bands_fails_before_the_upload(tmp_path, monkeypatch):
    job = _raw_job()
    _, _, total = _synthetic(job)
    measure = {"duration": round(total, 3), "f0_hz": 124.0, "wpm": 170.0, "bytes_per_sec": 9_700, "lufs": -16.0}
    code, fake, _ = _tick(tmp_path, monkeypatch, job, measure=measure)
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == "voice_out_of_band" and "f0_hz" in fail[3]
    assert "upload" not in fake.names()


def test_an_already_uploaded_object_skips_the_put(tmp_path, monkeypatch):
    body = {"upload_url": None, "already_uploaded": True, "path": "audio/weekly/x-0123456789ab.m4a"}
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(), upload_body=body)
    assert code == 0 and "put" not in fake.names() and "complete" in fake.names()


def test_a_409_on_complete_is_a_clean_stop(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(),
                          complete=api.NarratorTerminalError("409", status=409))
    assert code == 0 and "fail" not in fake.names()


def test_a_422_on_complete_is_reported(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(),
                          complete=api.NarratorTerminalError("POST /jobs/x/complete -> 422 LEARN_NARRATOR_REQUEST_INVALID: "
                                                             "timings: block 2 sentence 0", status=422))
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == "request_refused" and "block 2" in fail[3]


@pytest.mark.parametrize("over, code_", [
    ({"blocks": []}, "job_invalid"),
    ({"blocks": [{"i": 1, "text": "x", "role": "body"}]}, "job_invalid"),
    ({"blocks": [{"i": 0, "text": "  ** **  ", "role": "body"}]}, "job_invalid"),
    ({"blocks": [{"i": 0, "text": "Hello there.", "role": "aside"}]}, "job_invalid"),
    ({"engine": "gemini"}, "engine_unsupported"),
    ({"voice": "am_michael"}, "job_invalid"),
    ({"round": "1"}, "job_invalid"),
])
def test_an_invalid_job_is_reported(tmp_path, monkeypatch, over, code_):
    code, fake, seen = _tick(tmp_path, monkeypatch, _raw_job(**over))
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == code_ and seen["modes"] == []


def test_an_unreadable_claim_cannot_be_reported(tmp_path, monkeypatch):
    code, fake, _ = _tick(tmp_path, monkeypatch, _raw_job(claim_nonce=None))
    assert code == 1 and "fail" not in fake.names() and "hold" not in fake.names()


def test_a_voice_the_image_did_not_bake_is_reported(tmp_path, monkeypatch):
    code, fake, seen = _tick(tmp_path, monkeypatch, _raw_job("kokoro", voice={"voice": "bm_george"}))
    fail = next(c for c in fake.calls if c[0] == "fail")
    assert code == 1 and fail[2] == "weights_missing" and seen["modes"] == []


def test_missing_configuration_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda name: f"/usr/bin/{name}")
    env = _bake(tmp_path)
    assert m.main([], env=env, client_factory=lambda b, t: FakeAPI(None)) == 2
    empty = {"LEARN_NARRATOR_API_BASE": "https://x.test", "LEARN_NARRATOR_API_TOKEN": "t",
             "HF_HOME": str(tmp_path / "none"), "TORCH_HOME": str(tmp_path / "none")}
    fake = FakeAPI(None)
    assert m.main([], env=empty, client_factory=lambda b, t: fake) == 2
    assert fake.calls == []                              # never claims what it cannot render


def test_only_ready_engines_are_declared(tmp_path, monkeypatch):
    env = {**_bake(tmp_path, kokoro=False), "LEARN_NARRATOR_API_BASE": "https://x.test", "LEARN_NARRATOR_API_TOKEN": "t"}
    monkeypatch.setattr(m.shutil, "which", lambda name: f"/usr/bin/{name}")
    fake = FakeAPI(None)
    assert m.main([], env=env, client_factory=lambda b, t: fake) == 0
    assert fake.calls[0][1] == ["chatterbox"]
    env["LEARN_NARRATOR_ENGINES"] = "kokoro"
    fake = FakeAPI(None)
    assert m.main([], env=env, client_factory=lambda b, t: fake) == 2


def test_one_line_scrubs_urls_and_fail_codes_are_snake():
    assert m.one_line(f"PUT failed\n  for {SIGNED}  ") == "PUT failed for <url>"
    assert m.fail_code("Block_Gate") == "block_gate" and m.fail_code("no spaces allowed") == "worker_error"


def test_checks_stay_under_the_servers_cap():
    blocks = [{"i": i, "raw_seconds": 10.0, "words_per_sec": 2.5, "align_coverage": 1.0, "rerendered": False,
               "confidence": 0.9, "start_error": 0.05, "note": "x" * 200} for i in range(400)]
    out = m.bounded_checks({"blocks": blocks, "file": {}, "summary": {}})
    assert len(json.dumps(out)) <= m.MAX_CHECKS_CHARS
    assert set(out["blocks"][0]) == {"i", "raw_seconds", "words_per_sec", "align_coverage", "rerendered"}


# ══ 11. image, Railway record, constraints, bake ═════════════════════════════


def _no_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def test_the_railway_record():
    cfg = tomllib.loads((PKG / "railway.toml").read_text())
    assert cfg["deploy"]["cronSchedule"] == "*/20 * * * *"
    assert cfg["deploy"]["restartPolicyType"].lower() == "never"
    assert cfg["deploy"]["startCommand"] == "python -m learn_narrator.main"
    assert cfg["build"]["watchPatterns"] == ["/backend/learn_narrator/**"]
    assert "healthcheckPath" not in cfg["deploy"]


def test_the_dockerfile():
    text = (PKG / "Dockerfile").read_text()
    code = _no_comments(text)
    # Railway's Dockerfile validation refuses RUN flags it does not support — the first deploy
    # failed with "dockerfile invalid: flag '--network=none' is not supported at Line 57"
    # (2026-10-23). The other two Railway Dockerfiles use no RUN flag and no syntax directive.
    assert "RUN --" not in code
    assert not re.match(r"#\s*syntax\s*=", text.splitlines()[0])
    cpu = re.search(r"pip install [^\n]*--index-url https://download\.pytorch\.org/whl/cpu ([^\n\\&]+)", code)
    assert cpu and set(cpu.group(1).split()) == {"torch==2.6.0", "torchaudio==2.6.0"}
    assert "-r learn_narrator/requirements.txt -c learn_narrator/constraints.txt" in code
    assert "grep -qiE '^(nvidia-|triton)'" in code and "exit 1" in code
    assert "torch.version.cuda is None" in code
    assert re.search(r"HF_HOME=/app/models/hf", code) and re.search(r"TORCH_HOME=/app/models/torch", code)
    bake, copy_all = code.index("python -m learn_narrator.bake"), code.index("COPY . learn_narrator/")
    offline, smoke = code.index("HF_HUB_OFFLINE=1"), code.index("python -m learn_narrator.child smoke")
    assert bake < copy_all < offline < smoke
    # The dead proxy fails a child process that tries to download; it sits on the smoke RUN only —
    # an ENV line would reach the running worker, whose httpx follows proxy variables.
    smoke_run = code[code.rindex("\nRUN ", 0, smoke):smoke]
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert f"{var}=http://127.0.0.1:9" in smoke_run, var
    for env_line in re.findall(r"^ENV (?:[^\n]*\\\n)*[^\n]*", code, re.M):
        assert "PROXY" not in env_line.upper(), env_line
    assert "python -m learn_narrator.main --preflight" in code[smoke:]
    assert code.rstrip().endswith('CMD ["python", "-m", "learn_narrator.main"]')
    assert "SUPABASE" not in code


def _snapshot_socket(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(socket.socket, "connect_ex", socket.socket.connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)


def test_the_smoke_refuses_the_network_in_process(monkeypatch):
    """The replacement for `RUN --network=none`: an IP connection or a lookup fails as an unreachable
    network would, and is recorded; localhost and Unix sockets still work."""
    import errno
    import socket

    from learn_narrator import child

    _snapshot_socket(monkeypatch)                  # restored at teardown
    refused = child._refuse_network()

    with pytest.raises(OSError):
        socket.create_connection(("huggingface.co", 443), timeout=1)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError) as exc:
            s.connect(("93.184.216.34", 80))
        assert exc.value.errno == errno.ENETUNREACH
        assert s.connect_ex(("93.184.216.34", 80)) == errno.ENETUNREACH
    finally:
        s.close()
    assert len(refused) == 3 and "huggingface.co" in refused[0]

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        cli = socket.create_connection(srv.getsockname(), timeout=2)   # loopback stays open
        cli.close()
    finally:
        srv.close()
    a, b = socket.socketpair()                     # local IPC stays open
    a.sendall(b"x")
    assert b.recv(1) == b"x"
    a.close(), b.close()
    assert len(refused) == 3


def test_the_smoke_refuses_the_network_before_anything_is_imported():
    tree = ast.parse((PKG / "child.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "smoke")
    body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else fn.body
    first = body[0]
    assert isinstance(first, ast.Assign) and isinstance(first.value, ast.Call)
    assert getattr(first.value.func, "id", None) == "_refuse_network"
    assert "network_refused" in ast.unparse(fn)


def _pins(path: Path) -> Dict[str, str]:
    out = {}
    for ln in _no_comments(path.read_text()).splitlines():
        ln = ln.strip()
        if not ln or " @ " in ln:
            continue
        mm = re.fullmatch(r"([A-Za-z0-9_.\-]+)(?:\[[a-z,]+\])?==([0-9][A-Za-z0-9.+\-]*)", ln)
        assert mm, f"{path.name}: {ln!r} is not name==version"
        out[mm.group(1).lower().replace("_", "-")] = mm.group(2)
    return out


def test_the_constraints_resolve_both_engines_under_chatterbox_pins():
    pins = _pins(PKG / "constraints.txt")
    assert len(pins) > 100
    want = {"chatterbox-tts": "0.1.7", "kokoro": "0.9.4", "misaki": "0.9.4", "numpy": "1.26.4",
            "transformers": "5.2.0", "safetensors": "0.5.3", "torch": "2.6.0", "torchaudio": "2.6.0"}
    for k, v in want.items():
        assert pins.get(k) == v, k
    assert not [k for k in pins if k.startswith("nvidia-") or k == "triton"]


def test_setuptools_still_ships_pkg_resources_for_the_watermarker():
    """resemble-perth 1.0.1 (Chatterbox's watermarker) imports pkg_resources and hides the failure
    behind `PerthImplicitWatermarker = None`; setuptools removed pkg_resources in 82.0.0. The first
    Railway smoke died on exactly that (setuptools 84.2.1, 2026-10-23)."""
    pins = _pins(PKG / "constraints.txt")
    assert pins["resemble-perth"] == "1.0.1"
    major = int(pins["setuptools"].split(".")[0])
    assert major < 82, f"setuptools {pins['setuptools']} has no pkg_resources; perth would import as None"
    code = _no_comments((PKG / "Dockerfile").read_text())
    check = code.index("assert perth.PerthImplicitWatermarker is not None")
    assert code.index("-c learn_narrator/constraints.txt") < check < code.index("python -m learn_narrator.bake")
    assert "en-core-web-sm" not in pins                       # a URL requirement, not an index pin


def test_the_requirements_agree_with_the_constraints():
    req, con = _pins(PKG / "requirements.txt"), _pins(PKG / "constraints.txt")
    assert {k: v for k, v in req.items() if con.get(k) != v} == {}
    text = (PKG / "requirements.txt").read_text()
    assert re.search(r"^en_core_web_sm @ https://github\.com/explosion/spacy-models/releases/download/"
                     r"en_core_web_sm-3\.8\.0/en_core_web_sm-3\.8\.0-py3-none-any\.whl#sha256=[0-9a-f]{64}$", text, re.M)


def test_the_bake_lists_equal_the_runtimes():
    tree = _tree(PKG / "bake.py")
    for name in ("CHATTERBOX_REPO", "CHATTERBOX_FILES", "KOKORO_REPO", "KOKORO_FILES", "WAV2VEC2_CHECKPOINT"):
        got = ast.literal_eval(_top(tree, name))
        want = getattr(runtime, name)
        assert (tuple(got) if isinstance(want, tuple) else got) == want, name
    voices = ast.literal_eval(_top(tree, "KOKORO_VOICES"))
    assert voices[0] == "am_michael" and all(re.fullmatch(r"[ab][fm]_[a-z]+", v) for v in voices)
    assert re.fullmatch(r"[0-9a-f]{40}", ast.literal_eval(_top(tree, "CHATTERBOX_REVISION")))
    assert re.fullmatch(r"[0-9a-f]{40}", ast.literal_eval(_top(tree, "KOKORO_REVISION")))
    assert ast.literal_eval(_top(tree, "KOKORO_REPO")) == kk.KOKORO_REPO


def test_the_bake_refuses_to_run_offline_and_pins_refs_main(monkeypatch, capsys):
    from learn_narrator import bake

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert bake.main() == 2 and "HF_HUB_OFFLINE" in capsys.readouterr().err
    src = ast.unparse(_top(_tree(PKG / "bake.py"), "main"))
    assert src.count("_pin_main(") == 2 and "revision=CHATTERBOX_REVISION" in src and "revision=KOKORO_REVISION" in src


# ══ 12. the bench ════════════════════════════════════════════════════════════


@pytest.mark.parametrize("var", ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "LEARN_NARRATOR_API_TOKEN",
                                 "LEARN_NARRATOR_API_BASE"])
def test_the_bench_refuses_to_start_near_production(var, monkeypatch, capsys):
    def must_not_run(*a, **k):
        raise AssertionError("the bench started")
    monkeypatch.setattr(bench, "run", must_not_run)
    assert bench.main([], env={var: ""}) == 2
    assert var in capsys.readouterr().err


def test_the_bench_writes_only_under_tmp(monkeypatch, capsys):
    assert bench.under_tmp(Path("/tmp/learn_narrator_bench/x"))
    assert not bench.under_tmp(Path.home() / "bench")
    assert not bench.under_tmp(Path("/tmp/../etc"))
    assert bench.under_tmp(bench.default_out())
    args = bench.parse_args(["--out", str(Path.home() / "bench-out")])
    assert bench.run(args, env={}) == 2
    assert "not under /tmp" in capsys.readouterr().err


def test_the_fixture_is_about_1600_words_with_the_worst_cases():
    fx = bench.load_fixture()
    words = [len(b["text"].split()) for b in fx["blocks"]]
    assert 1500 <= sum(words) <= 1700 and fx["words"] == sum(words)
    by_source = {b["source"]: b["text"] for b in fx["blocks"]}
    dense = by_source["worst_case:figures_75"]
    assert len(dense.split()) == 75 and len(re.findall(r"\d", dense)) >= 30
    assert len(by_source["worst_case:three_word"].split()) == 3
    acr = by_source["worst_case:acronyms_dashes"]
    assert "—" in acr and re.search(r"\b[A-Z]{3,}\b", acr)
    for slug, (a, b) in fx["ab_segments"].items():
        assert all(fx["blocks"][k]["source"] == slug for k in range(a, b))
    job = bench.fixture_job(fx, "chatterbox", None)
    assert [b["i"] for b in job.blocks] == list(range(len(fx["blocks"])))


def test_the_ab_part_is_the_committed_catalog_text():
    fx = bench.load_fixture()
    head = subprocess.run(["git", "show", "4544fb96:frontend/ios/ios/Resources/MoneyMoves/money_moves.json"],
                          cwd=BACKEND, capture_output=True)
    if head.returncode != 0:
        pytest.skip("git history not available")
    assert hashlib.sha256(head.stdout).hexdigest() == fx["source_sha256"]
    articles = {a["slug"]: a for a in json.loads(head.stdout)["articles"]}
    amazon = articles["how-amazon-built-its-moat"]
    a, b = fx["ab_segments"]["how-amazon-built-its-moat"]
    assert fx["blocks"][a]["text"] == amazon["title"] and fx["blocks"][a + 1]["text"] == amazon["subtitle"]


def _grade(**over):
    render = {"emotion_adv": 0.65, "blocks": [
        {"i": 0, "raw_seconds": 45.0, "words_per_sec": 2.6, "rerendered": False,
         "calls": [{"raw_seconds": 25.0, "words_per_sec": 2.6}, {"raw_seconds": 20.0, "words_per_sec": 2.6}]},
        {"i": 1, "raw_seconds": 1.2, "words_per_sec": 2.5, "rerendered": True, "first_reason": "1.6 spoken words/s"}]}
    table = [{"sentences": [{}] * 3}, {"sentences": [{}]}]
    checks = [{"align_coverage": 1.0, "start_error": 0.04}, {"align_coverage": 1.0, "start_error": 0.1}]
    file_m = {"duration": 30.0, "f0_hz": 134.0, "wpm": 158.0, "bytes_per_sec": 12_100, "lufs": -16.0}
    kw = dict(engine="chatterbox", render=render, table=table, block_checks=checks, table_problems=[],
              file_m=file_m, wall_seconds=120 * 60, peak_rss=5 * 1024 ** 3)
    kw.update(over)
    return bench.grade(**kw)


def test_grading_passes_a_good_run_and_leaves_a5_to_the_owner():
    g = _grade()
    assert all(g[k]["pass"] for k in ("A1", "A2", "A3", "A4", "A6", "exaggeration"))
    assert g["A5"]["pass"] is None and g["A3"]["rerendered"] == 1
    assert g["A3"]["split_blocks"] == [0] and g["A3"]["max_call_seconds"] == 25.0   # a split block is not truncated


@pytest.mark.parametrize("over, failed", [
    ({"wall_seconds": 151 * 60}, "A1"),
    ({"peak_rss": 6 * 1024 ** 3 + 1}, "A2"),
    ({"table_problems": ["block 1 sentence 0 spans 0.01s"]}, "A6"),
    ({"block_checks": [{"align_coverage": 1.0, "start_error": 0.2}, {"align_coverage": 1.0, "start_error": 0.1}]}, "A6"),
    ({"block_checks": [{"align_coverage": 2 / 3, "start_error": 0.04}, {"align_coverage": 1.0, "start_error": 0.1}]}, "A6"),
    ({"file_m": {"duration": 30.0, "f0_hz": 125.0, "wpm": 158.0, "bytes_per_sec": 12_100, "lufs": -16.0}}, "A4"),
])
def test_grading_fails_each_bar(over, failed):
    assert _grade(**over)[failed]["pass"] is False


def test_grading_catches_truncation_and_the_exaggeration_default():
    render = {"emotion_adv": 0.5, "blocks": [
        {"i": 0, "raw_seconds": 39.6, "words_per_sec": 2.4},
        {"i": 1, "raw_seconds": 5.0, "words_per_sec": 4.0},
        {"i": 2, "raw_seconds": 60.0, "words_per_sec": 2.4,
         "calls": [{"raw_seconds": 20.0, "words_per_sec": 2.4}, {"raw_seconds": 39.7, "words_per_sec": 2.4}]}]}
    g = _grade(render=render)
    assert g["A3"]["pass"] is False and g["A3"]["truncated"] == [0, 2] and g["A3"]["off_band"] == [1]
    assert g["exaggeration"]["pass"] is False
