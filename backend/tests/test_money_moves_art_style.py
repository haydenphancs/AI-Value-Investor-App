"""The shared cover-art SYSTEM (`app/services/money_moves_art_style.py`) — moved out of
`scripts/generate_money_moves_art.py` on 2026-11-01 so the weekly engine draws in the same style.

THE guard here is the re-roll guard: every catalog plate is paid and nondeterministic, and the script
decides a plate is up to date by comparing sha1(prompt)[:12] with the manifest's `art_prompt_sha1`.
So the move must be byte-identical, and stay that way: for EVERY manifest that records a hash, the
prompt recomputed from THIS module (and through the script) must hash to it — and reproduce the
manifest's stored prompt byte for byte. Then: the script holds no second copy of a string (it
re-exports this module's objects), the module is pure, and the script's file derivatives and the
engine's in-memory ones are the same bytes.

Hermetic: no network, no Supabase (Pillow draws the synthetic images).
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

from app.services import money_moves_art_style as style

BACKEND = Path(__file__).resolve().parents[1]
ART_DIR = BACKEND / "data/money_moves_art"
STYLE_SRC = BACKEND / "app/services/money_moves_art_style.py"
SCRIPT_SRC = BACKEND / "scripts/generate_money_moves_art.py"
COVER_SRC = BACKEND / "app/services/learn_weekly/cover_art.py"

sys.path.insert(0, str(BACKEND / "scripts"))
import generate_money_moves_art as art  # noqa: E402


def _hashed_manifests() -> list[dict]:
    out = []
    for p in sorted(ART_DIR.glob("*.manifest.json")):
        man = json.loads(p.read_text())
        if man.get("art_prompt_sha1"):
            out.append(man)
    return out


def _sha12(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


# ── 1. the re-roll guard ──────────────────────────────────────────────────────────────────────


def test_every_manifest_hash_is_reproduced_by_the_shared_module():
    """Approved plates must never re-roll: sha1(prompt_for_slug(slug))[:12] == art_prompt_sha1 for
    every manifest, AND the module alone (its ARTICLES row, its palette override, `prompt_for`)
    rebuilds the manifest's stored prompt byte for byte."""
    if not ART_DIR.exists():
        pytest.skip("no backend/data/money_moves_art in this checkout")
    mans = _hashed_manifests()
    assert len(mans) >= 20, f"only {len(mans)} hashed manifests — the guard would prove little"
    drift = []
    for man in mans:
        slug, recorded = man["slug"], man["art_prompt_sha1"]
        if slug not in style.ARTICLES:
            # Reported, never a KeyError / SystemExit mid-loop: name EVERY plate at risk in one run.
            drift.append(f"{slug}: no ARTICLES row in money_moves_art_style (manifest {recorded}) — "
                         "a generate run would ask --auto for a new subject and re-roll it")
            continue
        via_script = _sha12(art.prompt_for_slug(slug))
        category, treatment, subject = style.ARTICLES[slug]
        direct = style.prompt_for(subject, category, treatment, style.PALETTE_OVERRIDES.get(slug))
        if via_script != recorded:
            drift.append(f"{slug}: script prompt {via_script} != manifest {recorded}")
        if _sha12(direct) != recorded:
            drift.append(f"{slug}: module prompt {_sha12(direct)} != manifest {recorded}")
        if man.get("art_prompt") is not None and direct != man["art_prompt"]:
            drift.append(f"{slug}: module prompt is not the manifest's stored prompt byte for byte")
    assert not drift, ("an approved catalog plate would RE-ROLL at full cost on the next generate "
                       "run:\n  " + "\n  ".join(drift))


def test_every_hashed_manifest_has_its_slug_in_the_shared_table():
    """A manifest whose slug left ARTICLES would fall back to --auto's cached subject (or none) and
    re-roll: every hashed plate keeps its authored row."""
    missing = [m["slug"] for m in _hashed_manifests() if m["slug"] not in style.ARTICLES]
    assert not missing, missing


#: Plates generated in the main checkout on 2026-11-01 from hand-written subjects (their manifests
#: were untracked when the table moved here). Pinned so the rows can never be dropped in a merge: each
#: hash is the manifest's `art_prompt_sha1`, so this holds even in a checkout without the manifests.
_AUTHORED_2026_11_01 = {
    "coca-cola-vs-pepsico": "6d803a6d8af9",
    "kodak-inventing-the-future-it-couldnt-sell": "e72a0afd0f65",
    "mcdonalds-the-real-estate-empire": "404f24d336ed",
}


@pytest.mark.parametrize("slug, recorded", sorted(_AUTHORED_2026_11_01.items()))
def test_the_authored_2026_11_01_rows_reproduce_their_plates(slug, recorded):
    assert slug in style.ARTICLES, f"{slug} lost its ARTICLES row — its paid plate would re-roll"
    category, treatment, subject = style.ARTICLES[slug]
    assert _sha12(style.prompt_for(subject, category, treatment,
                                   style.PALETTE_OVERRIDES.get(slug))) == recorded
    assert _sha12(art.prompt_for_slug(slug)) == recorded


def test_articles_has_no_duplicate_key():
    """A dict literal keeps the LAST of two equal keys silently, so a row inserted twice (an insert
    tool re-run against this module) or a second, edited copy would hide which subject is live."""
    tree = ast.parse(STYLE_SRC.read_text(encoding="utf-8"))
    tables = [node.value for node in tree.body
              if isinstance(node, (ast.Assign, ast.AnnAssign))
              and any(isinstance(t, ast.Name) and t.id == "ARTICLES"
                      for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    assert len(tables) == 1 and isinstance(tables[0], ast.Dict), "ARTICLES is not one dict literal"
    keys = [k.value for k in tables[0].keys if isinstance(k, ast.Constant)]
    assert len(keys) == len(tables[0].keys) == len(style.ARTICLES), "a non-literal or repeated key"
    assert len(set(keys)) == len(keys), sorted({k for k in keys if keys.count(k) > 1})


def test_the_guard_is_not_vacuous():
    """One changed character in a subject changes the hash the guard compares."""
    slug = next(iter(style.ARTICLES))
    category, treatment, subject = style.ARTICLES[slug]
    base = style.prompt_for(subject, category, treatment, style.PALETTE_OVERRIDES.get(slug))
    assert _sha12(base) != _sha12(style.prompt_for(subject + ".", category, treatment,
                                                   style.PALETTE_OVERRIDES.get(slug)))


# ── 2. one copy of every string ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "BASE", "TREATMENTS", "PALETTES", "ARTICLES", "PLACEHOLDER_TOPICS", "PALETTE_OVERRIDES",
    "STAGED_ARTICLES", "DERIVATIVES", "_SUBJECT_PROMPT", "_CATEGORY_NOTE", "_SHAPE", "_BANNED_SUBJECT",
    "default_treatment", "prompt_for", "_examples_for",
])
def test_the_script_re_exports_the_shared_objects(name):
    """The SAME object, not an equal copy: a hand-duplicated string would drift silently."""
    assert getattr(art, name) is getattr(style, name)


def _assigned_names(path: Path) -> set:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
        for t in targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


_SYSTEM_NAMES = {"BASE", "TREATMENTS", "PALETTES", "ARTICLES", "PALETTE_OVERRIDES", "PLACEHOLDER_TOPICS",
                 "STAGED_ARTICLES",
                 "_SUBJECT_PROMPT", "_CATEGORY_NOTE", "_SHAPE", "_BANNED_SUBJECT", "DERIVATIVES",
                 "prompt_for", "default_treatment", "_examples_for"}


@pytest.mark.parametrize("path", [SCRIPT_SRC, COVER_SRC], ids=["script", "cover_art"])
def test_no_caller_defines_its_own_copy_of_the_system(path):
    clash = _assigned_names(path) & _SYSTEM_NAMES
    assert not clash, f"{path.name} defines {sorted(clash)} — import them from money_moves_art_style"


def test_the_style_module_is_pure():
    """No app settings, no network, no Supabase: the script imports it standalone."""
    imported = set()
    for node in ast.walk(ast.parse(STYLE_SRC.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(m.startswith("app") for m in imported), imported
    assert not imported & {"httpx", "requests", "supabase", "google.genai", "numpy"}, imported


# ── 3. derivatives: the script's files and the engine's bytes are the same ────────────────────


def _png(w: int, h: int) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h), (40, 60, 90))
    ImageDraw.Draw(im).ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(200, 150, 60))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_in_memory_derivatives_have_the_shipping_sizes():
    from PIL import Image

    master, size = style.encode_master(_png(1600, 900))
    assert size == (1600, 900)
    out = style.derive_all_jpeg(master)
    assert set(out) == set(style.DERIVATIVES) == {"hero", "card"}
    for name, data in out.items():
        with Image.open(io.BytesIO(data)) as im:
            assert im.format == "JPEG" and im.size == style.DERIVATIVES[name]


def test_an_off_aspect_master_is_centre_cropped_never_stretched():
    master, _ = style.encode_master(_png(1000, 1000))
    data, cropped = style.derive_jpeg(master, 640, 360)
    assert cropped is True
    _, exact = style.derive_jpeg(style.encode_master(_png(1600, 900))[0], 640, 360)
    assert exact is False


def test_the_script_and_the_engine_derive_identical_bytes(tmp_path):
    """The engine resamples from the same q95 master with the same operations as the script's
    `_write_master` + `_derive`, so a weekly plate is made exactly like a catalog one."""
    blob = _png(1376, 768)
    master_file = tmp_path / "x.art.jpg"
    art._write_master(blob, master_file)
    master_bytes, _ = style.encode_master(blob)
    assert master_file.read_bytes() == master_bytes
    for name, (w, h) in style.DERIVATIVES.items():
        rec = art._derive(master_file, tmp_path / f"x.{name}.jpg", w, h)
        assert (tmp_path / f"x.{name}.jpg").read_bytes() == style.derive_jpeg(master_bytes, w, h)[0]
        assert rec["width"] == w and rec["height"] == h


def test_a_truncated_image_is_refused():
    blob = _png(1600, 900)
    with pytest.raises(Exception):
        style.encode_master(blob[: len(blob) // 3])


# ── 4. the subject checks ─────────────────────────────────────────────────────────────────────


def test_subject_prompt_is_the_auto_prompt():
    p = style.subject_prompt("How Chips Became Scarce", "Why fabs matter.", "battles")
    assert "ARTICLE TITLE: How Chips Became Scarce" in p and "Why fabs matter." in p
    assert style._SHAPE["battles"] in p and style._CATEGORY_NOTE["battles"] in p
    assert style._examples_for("battles") in p
    # an unknown category falls back to blueprints, never a KeyError
    assert style._SHAPE["blueprints"] in style.subject_prompt("t", "s", "nonsense")


@pytest.mark.parametrize("text, category, ok", [
    ("two identical unbranded aluminium drinks cans standing side by side, both completely blank",
     "battles", True),
    ("a single unbranded aluminium drinks can standing upright on a plain surface, completely blank",
     "battles", False),
    ("a heavy cast-iron flywheel on a machined shaft, seen three-quarters on so the rim reads",
     "blueprints", True),
    ("a stack of amazon shipping boxes with tape across every seam, all completely unmarked",
     "blueprints", False),
    ("two hands holding a cracked coin over a dark desk with nothing written anywhere on it",
     "valueTraps", False),
    ("a small coin", "valueTraps", False),
])
def test_subject_problem(text, category, ok):
    assert (style.subject_problem(text, category) is None) is ok
