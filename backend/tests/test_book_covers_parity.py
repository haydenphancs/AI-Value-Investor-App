"""
Guards for the book-cover pipeline.

NOT a schema-parity test, and deliberately so: covers have no Pydantic response model,
no endpoint and no Codable decode — the URL is a compile-time String literal in
generated Swift. There is nothing for a decode-parity test to pin, and writing one
would be cargo cult. What IS worth asserting is that the four pipeline stages agree
with each other and with the app's own catalogue.

Category 1 (math/transform per .claude/rules/testing.md): pure, no network, no Supabase.

    cd backend && ./venv/bin/pytest tests/test_book_covers_parity.py -q
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DATA = ROOT / "data/book_covers"
SWIFT = REPO / "frontend/ios/ios/Models/BookCoverContent.swift"
LEARN_MODELS = REPO / "frontend/ios/ios/Models/LearnModels.swift"
ATOM = REPO / "frontend/ios/ios/Views/Atoms/BookCoverImage.swift"

sys.path.insert(0, str(ROOT / "scripts"))
from generate_book_cover_art import BOOKS  # noqa: E402

HERO_SIZE = (480, 660)
THUMB_SIZE = (240, 330)
MAX_BYTES = 400 * 1024


def _manifests():
    out = {}
    for p in sorted(DATA.glob("*.manifest.json")):
        m = json.loads(p.read_text())
        out[m["curriculum_order"]] = m
    return out


def _swift_entries():
    """(normalizedKey -> {thumbURL, heroURL}) as actually baked into the app."""
    src = SWIFT.read_text()
    body = src[src.find("static let byTitle"):src.find("static let byOrder")]
    entries = {}
    for key, thumb, hero in re.findall(
        r'"([a-z0-9]+)":\s*BookCoverArt\([^)]*?thumbURL:\s*"([^"]+)",\s*heroURL:\s*"([^"]+)"',
        body, re.S,
    ):
        entries[key] = {"thumb": thumb, "hero": hero}
    return entries


def _normalized(title: str) -> str:
    return "".join(ch for ch in title.lower() if ch.isalnum())


# ---------------------------------------------------------------- coverage
def test_every_book_has_a_manifest():
    missing = [o for o in BOOKS if o not in _manifests()]
    assert not missing, f"books with no manifest: {missing}"


def test_every_manifest_is_composed():
    unbuilt = [o for o, m in _manifests().items() if not m.get("masters")]
    assert not unbuilt, f"books generated but never composed: {unbuilt}"


# --------------------------------------------- generated Swift <-> manifests
def test_generated_swift_matches_the_manifests():
    """The highest-value guard here.

    'Seeded but forgot to re-run the codegen' is the likeliest operator error in a
    four-stage chain, and it produces a silently stale app rather than a crash. The
    audio pipeline has no equivalent check.
    """
    mans, entries = _manifests(), _swift_entries()
    assert entries, "no entries parsed out of BookCoverContent.swift"

    expected = {_normalized(m["title"]) for m in mans.values() if m.get("masters")}
    assert set(entries) == expected, (
        f"BookCoverContent.swift is out of sync with the manifests.\n"
        f"  only in Swift:     {sorted(set(entries) - expected)}\n"
        f"  only in manifests: {sorted(expected - set(entries))}\n"
        f"Re-run: ./venv/bin/python scripts/gen_book_covers_swift.py")

    for m in mans.values():
        if not m.get("masters"):
            continue
        e = entries[_normalized(m["title"])]
        for label in ("thumb", "hero"):
            assert e[label].endswith("/" + m["masters"][label]["file"]), (
                f"{m['title']}: baked {label} URL {e[label]} does not end with "
                f"{m['masters'][label]['file']}")


def test_urls_point_at_the_public_covers_bucket():
    """A bucket rename or path drift would ship a 404 to every user."""
    for key, e in _swift_entries().items():
        for label, url in e.items():
            assert url.startswith("https://"), f"{key} {label}: not https"
            assert "/storage/v1/object/public/book-covers/covers/" in url, (
                f"{key} {label}: not the public book-covers path — {url}")


# ------------------------------------------------- join key against the app
def test_every_library_book_title_resolves_to_a_cover():
    """Defends the title join key. A punctuation or capitalisation edit in
    LearnModels.swift would otherwise silently blank a cover."""
    src = LEARN_MODELS.read_text()
    block = src[src.find("static let sampleData: [LibraryBook]"):]
    # only the title that OPENS a LibraryBook literal — a bare `title:` search also
    # matches keyHighlights / coreChapters and yields phantom failures
    titles = re.findall(r'LibraryBook\(\s*title:\s*"([^"]+)"', block)
    assert titles, "could not find any LibraryBook titles — did the model change shape?"
    have = set(_swift_entries())
    missing = [t for t in titles if _normalized(t) not in have]
    assert not missing, f"LibraryBook titles with no cover: {missing}"


def test_normalization_is_injective_over_the_set():
    keys = [_normalized(m["title"]) for m in _manifests().values()]
    assert len(set(keys)) == len(keys), "two books normalize to the same key"


def test_manifest_titles_and_authors_match_the_ios_catalogue():
    """The cover byline is drawn 8pt from `Text(book.author)` on the same card, so a
    divergence reads to a user as a data bug."""
    src = LEARN_MODELS.read_text()
    block = src[src.find("static let sampleData: [LibraryBook]"):]
    pairs = re.findall(r'LibraryBook\(\s*title:\s*"([^"]+)",\s*author:\s*"([^"]+)"', block)
    app = {_normalized(t): a for t, a in pairs}
    for m in _manifests().values():
        key = _normalized(m["title"])
        if key in app:
            assert m["author"] == app[key], (
                f"{m['title']}: manifest author {m['author']!r} != "
                f"LearnModels {app[key]!r}")


# ------------------------------------------------------------ image invariants
def _pil():
    return pytest.importorskip("PIL.Image", reason="Pillow not installed")


def test_master_dimensions_and_size():
    Image = _pil()
    for order, m in _manifests().items():
        if not m.get("masters"):
            continue
        for label, want in (("thumb", THUMB_SIZE), ("hero", HERO_SIZE)):
            rec = m["masters"][label]
            p = DATA / rec["file"]
            if not p.exists():
                pytest.skip(f"{rec['file']} not present")
            with Image.open(p) as im:
                assert im.size == want, f"book {order} {label}: {im.size} != {want}"
                assert im.mode == "RGB", f"book {order} {label}: mode {im.mode}"
            assert p.stat().st_size <= MAX_BYTES, (
                f"book {order} {label}: {p.stat().st_size // 1024} KB over "
                f"{MAX_BYTES // 1024} KB")


def test_master_hashes_match_the_manifest():
    import hashlib
    for order, m in _manifests().items():
        if not m.get("masters"):
            continue
        for label in ("thumb", "hero"):
            rec = m["masters"][label]
            p = DATA / rec["file"]
            if not p.exists():
                pytest.skip(f"{rec['file']} not present")
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            assert got == rec["sha256"], (
                f"book {order} {label}: file changed since compose — re-run "
                f"compose_book_cover.py so the manifest and the bytes agree")


def test_legibility_floor():
    """Both inks must clear AA body text against what is actually behind them."""
    for order, m in _manifests().items():
        leg = m.get("legibility")
        if not leg:
            continue
        assert leg["title_contrast"] >= 4.5, f"book {order}: title {leg['title_contrast']}:1"
        assert leg["tagline_contrast"] >= 4.5, (
            f"book {order}: tagline {leg['tagline_contrast']}:1 — the tagline is the "
            f"weakest ink and the scrim is solved for it")


def test_titles_are_all_one_size():
    """A set is only a set if the titles match. Per-book fitting made short titles
    large and long ones small."""
    caps = {(m.get("title_cap_hero_px"), m.get("title_cap_thumb_px"))
            for m in _manifests().values() if m.get("masters")}
    assert len(caps) == 1, f"title cap differs across the set: {caps}"


def test_palette_is_balanced_and_never_runs_three_deep():
    """Colour is doing real work: it is what separates ten dark covers in a scroll.

    The shelf is W C C W W C C W W C — a balanced 5/5 split with no run longer than
    two, NOT strict alternation. Strict alternation was asserted first and is simply
    not what the set is; the useful invariant is that the palette never goes flat for
    three books running, which is where a scroll starts to look monotonous.
    """
    mans = _manifests()
    orders = sorted(o for o in mans if mans[o].get("palette"))
    pals = [mans[o]["palette"] for o in orders]
    assert pals.count("warm") == pals.count("cool"), (
        f"palette split is not balanced: {pals.count('warm')} warm / "
        f"{pals.count('cool')} cool")
    run = longest = 1
    for a, b in zip(pals, pals[1:]):
        run = run + 1 if a == b else 1
        longest = max(longest, run)
    assert longest <= 2, f"a run of {longest} consecutive books shares one palette: {pals}"


# ----------------------------------------------------------------- iOS wiring
def test_atom_never_force_unwraps_the_cover_lookup():
    src = ATOM.read_text()
    assert "forTitle(" in src, "the atom should resolve via BookCoverArt.forTitle"
    assert "byTitle[" not in src or "!" not in src.split("byTitle[")[1][:40], (
        "never force-unwrap the cover table — a missing key must fall back to the gradient")


def test_no_call_site_still_draws_its_own_book_gradient():
    """The four render sites shared two copies of an 18-line `switch book.title`
    gradient. They must all go through the atom now."""
    for rel in ("Views/Molecules/LibraryBookCard.swift",
                "Views/Molecules/EducationBookCard.swift",
                "Views/Molecules/SearchBookCard.swift",
                "Views/Screens/BookDetailView.swift"):
        src = (REPO / "frontend/ios/ios" / rel).read_text()
        assert "BookCoverImage(" in src, f"{rel} does not use the shared atom"
        assert "private var bookCoverGradient" not in src, (
            f"{rel} still defines its own cover gradient")


def test_search_book_card_no_longer_references_a_missing_asset():
    """It used to call Image(book.coverImageName) for assets that have never existed
    in Assets.xcassets, with the title fallback hardcoded to .opacity(0) — so it
    rendered no cover AND no title."""
    src = (REPO / "frontend/ios/ios/Views/Molecules/SearchBookCard.swift").read_text()
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("//"))
    assert "Image(book.coverImageName)" not in code
    assert ".opacity(0)" not in code
