"""The book grounding join must hold across three independently-maintained places.

A book chat is only grounded if a chain of hand-maintained data lines up:

    EducationBook.title  --title match-->  LibraryBook.curriculumOrder
                                                  |
                              +-------------------+-------------------+
                              v                                       v
              BookCoreChapter.listsByOrder                    _BOOK_VOICES (backend)
              (the guide outline we send)                     (the method voice)

Every link is a separate literal list written by hand, and a break in any of them is
SILENT: the chat still opens, the chip still names a book, and the
backend receives nothing. That overclaim is exactly what this change set exists to end, so
the join is pinned rather than trusted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.agents.book_voice_prompt import BOOK_VOICE_ORDERS, book_display_title

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_LEARN_MODELS = _IOS / "Models/LearnModels.swift"
_BOOKS_CONTENT = _IOS / "Models/BooksContent.swift"
_PATH_VM = _IOS / "ViewModels/InvestorPathViewModel.swift"


def _src(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text()


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def _library_books() -> dict[int, str]:
    """curriculumOrder -> title, parsed from the LibraryBook literals."""
    src = _strip_comments(_src(_LEARN_MODELS))
    out: dict[int, str] = {}
    for m in re.finditer(r"LibraryBook\((.{0,1200}?)curriculumOrder:\s*(\d+)", src, re.S):
        title = re.search(r'title:\s*"([^"]+)"', m.group(1))
        if title:
            out[int(m.group(2))] = title.group(1)
    return out


def _education_book_titles() -> list[str]:
    src = _strip_comments(_src(_LEARN_MODELS))
    return [
        m.group(1)
        for blob in re.finditer(r"EducationBook\((.{0,900}?)\)", src, re.S)
        for m in [re.search(r'title:\s*"([^"]+)"', blob.group(1))]
        if m
    ]


def _core_list_orders() -> set[int]:
    src = _strip_comments(_src(_BOOKS_CONTENT))
    start = src.index("listsByOrder")
    return {int(m.group(1)) for m in re.finditer(r"^\s{4,8}(\d+):\s*\[", src[start:], re.M)}


# ── the joins ────────────────────────────────────────────────────────────────────────


def test_every_education_book_resolves_to_a_library_book():
    """The Learn tab renders EducationBook but grounds via LibraryBook, matched BY TITLE
    across two independent literal lists in one file. A typo or a retitle un-grounds the
    chat silently."""
    titles = set(_library_books().values())
    missing = [t for t in _education_book_titles() if t not in titles]
    assert not missing, f"EducationBook titles with no LibraryBook: {missing}"


def test_every_library_book_has_a_backend_voice():
    """The parity test for this feature: an eleventh book must not ship with a grounding
    chip and no personality."""
    assert set(_library_books()) == set(BOOK_VOICE_ORDERS)


def test_every_backend_voice_title_matches_the_ios_catalogue():
    """The source pill's detail comes from the backend registry; the chip's comes from the
    iOS catalogue. If they drift, one screen names a different book than the other."""
    for order, title in _library_books().items():
        assert book_display_title(str(order)) == title, order


def test_every_library_book_has_authored_cores():
    """`studyGuideContext` sends the core outline. A book with no cores would send a header
    and a trailer and nothing else — a chip claiming a guide that carries no content."""
    missing = sorted(set(_library_books()) - _core_list_orders())
    assert not missing, f"curriculum orders with no authored cores: {missing}"


def test_the_journey_deep_dive_book_resolves():
    """The Journey card carries its own hardcoded order; it must name a real book."""
    src = _strip_comments(_src(_PATH_VM))
    m = re.search(
        r"JourneyDeepDiveBook\(\s*title:\s*\"([^\"]+)\".*?curriculumOrder:\s*(\d+)",
        src, re.S,
    )
    assert m, "deepDiveBook must declare a curriculumOrder"
    title, order = m.group(1), int(m.group(2))
    assert _library_books().get(order) == title, (
        f"deepDiveBook says order {order} is {title!r}, catalogue disagrees"
    )


def test_the_scan_is_not_vacuous():
    library = _library_books()
    assert len(library) >= 10
    assert len(_education_book_titles()) >= 3
    assert len(_core_list_orders()) >= 10
    assert all(isinstance(k, int) and v for k, v in library.items())


# ── the grounding digest must fit its budget without degrading ───────────────────────
#
# `LibraryBook.studyGuideContext` has a degradation ladder, so an oversized digest can
# never 422 — but a ladder that fires is a ladder that silently drops content the reader
# asked for. Measured from the real Swift catalogue: the worst overview is ~2.9k and the
# worst passage ~3.4k against a 4,000 budget. These pin that headroom, so a book authored
# with far more cores fails HERE rather than quietly losing half its outline in the reader.

_CLIENT_BUDGET = 4000          # studyGuideContext's default maxChars
_PASSAGE_CAP = 2600            # CoreChapterContent.plainTextForGrounding default
_HEADER_TRAILER_ALLOWANCE = 400
_HIGHLIGHTS_ALLOWANCE = 500    # 4 key highlights, only in .overview


def _core_entries() -> dict[int, list[tuple[str, str]]]:
    """order -> [(title, description)] parsed from BookCoreChapter.listsByOrder."""
    src = _strip_comments(_src(_BOOKS_CONTENT))
    body = src[src.index("listsByOrder"):]
    out: dict[int, list[tuple[str, str]]] = {}
    for m in re.finditer(r"^\s{4,8}(\d+):\s*\[", body, re.M):
        seg, depth, end = body[m.end():], 1, 0
        for i, ch in enumerate(seg):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        entries = re.findall(
            r'BookCoreChapter\(\s*number:\s*\d+,\s*title:\s*"((?:[^"\\]|\\.)*)",'
            r'\s*description:\s*"((?:[^"\\]|\\.)*)"',
            seg[:end], re.S,
        )
        if entries:
            out[int(m.group(1))] = entries
    return out


def _outline_len(entries: list[tuple[str, str]], *, with_descriptions: bool) -> int:
    lines = [
        f"{i}. {t} — {d}" if with_descriptions and d else f"{i}. {t}"
        for i, (t, d) in enumerate(entries, start=1)
    ]
    return len("Core outline:\n" + "\n".join(lines))


@pytest.mark.parametrize("order", sorted(_core_entries()))
def test_the_overview_digest_fits_without_degrading(order: int):
    """The card entry points send the FULL outline with descriptions. If this fails the
    reader silently loses the descriptions — which are where the actual claim lives."""
    size = (
        _outline_len(_core_entries()[order], with_descriptions=True)
        + _HEADER_TRAILER_ALLOWANCE
        + _HIGHLIGHTS_ALLOWANCE
    )
    assert size <= _CLIENT_BUDGET, (
        f"book {order}'s overview digest is ~{size} chars against a {_CLIENT_BUDGET} "
        "budget — studyGuideContext will start dropping core descriptions"
    )


@pytest.mark.parametrize("order", sorted(_core_entries()))
def test_the_passage_digest_fits_without_degrading(order: int):
    """The in-reader chat sends the core's full text PLUS the outline as a map. If this
    fails the map gets halved, and 'how does this connect to core 9?' stops working."""
    size = (
        _outline_len(_core_entries()[order], with_descriptions=False)
        + _PASSAGE_CAP
        + _HEADER_TRAILER_ALLOWANCE
    )
    assert size <= _CLIENT_BUDGET, (
        f"book {order}'s passage digest is ~{size} chars against a {_CLIENT_BUDGET} budget"
    )


def test_the_budget_constants_match_the_swift_source():
    """Anti-drift: these bounds are meaningless if the Swift defaults move."""
    models = _strip_comments(_src(_LEARN_MODELS))
    m = re.search(r"func studyGuideContext\([^)]*maxChars:\s*Int\s*=\s*(\d+)", models, re.S)
    assert m and int(m.group(1)) == _CLIENT_BUDGET
    detail = _strip_comments(_src(_IOS / "Models/BookCoreDetailModels.swift"))
    d = re.search(r"func plainTextForGrounding\(maxChars:\s*Int\s*=\s*(\d+)\)", detail)
    assert d and int(d.group(1)) == _PASSAGE_CAP
